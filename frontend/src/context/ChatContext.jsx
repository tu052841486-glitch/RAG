import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import {
  askQuestion,
  listConversations,
  upsertConversation,
  deleteConversationApi,
  renameConversationApi,
  pinConversationApi,
} from '../services/api';
import { useAuth } from './AuthContext';

const WELCOME = {
  role: 'bot',
  text: '你好！我是農藥博士 🌿\n\n你可以問我：\n• 某作物可以用哪些農藥？\n• 某農藥的安全採收期是幾天？\n• 某病蟲害用哪種農藥防治？',
  sources: [],
};

function makeFreshConv() {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    title: '新對話',
    messages: [WELCOME],
    pinned: false,
    updatedAt: Date.now(),
  };
}

function makeTitle(question) {
  const t = question.trim();
  return t.length > 18 ? t.slice(0, 18) + '…' : t;
}

// 依「釘選優先、其次更新時間新到舊」排序，跟後端排序一致
function sortConvs(list) {
  return [...list].sort((a, b) => {
    if (!!b.pinned !== !!a.pinned) return (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0);
    return b.updatedAt - a.updatedAt;
  });
}

const ChatContext = createContext(null);

// 對話狀態放在 App 最上層（AuthProvider 之下、Routes 之上）。
//
// 登入者：對話存後端（換裝置、換設備登入同一帳號都看得到自己的、且看不到別人的）。
// 訪客：只在記憶體保留單一對話，不落地、不上傳。
export function ChatProvider({ children }) {
  const { token } = useAuth();
  const [conversations, setConversations] = useState(() => [makeFreshConv()]);
  const [activeId, setActiveId] = useState(() => null);
  const [pendingIds, setPendingIds] = useState([]);
  const [loadingList, setLoadingList] = useState(false);

  // 記錄目前有效的 token，讓非同步流程結束時能判斷「這中間有沒有登出/換帳號」，
  // 避免舊流程把資料寫進已經換掉的帳號、或把畫面蓋回舊狀態（race condition 防護）。
  const tokenRef = useRef(token);
  useEffect(() => { tokenRef.current = token; }, [token]);
  const lastCropRef = useRef(null);
  const lastPestRef = useRef(null);

  // 把單一對話存回後端（僅登入者）。回傳 Promise 讓呼叫端可以 await，確保真的寫完。
  const persist = async (conv) => {
    if (!tokenRef.current || !conv) return;
    try { await upsertConversation(conv); } catch {}
  };

  // 登入/登出切換時：登入→從後端載回歷史；登出→回到訪客的單一暫存對話
  useEffect(() => {
    let cancelled = false;
    const myToken = token; // 綁定這次 effect 的 token

    async function loadForLogin() {
      setLoadingList(true);
      try {
        const res = await listConversations();
        // 若載入期間又登出/換帳號，丟棄這次結果，避免蓋掉新狀態
        if (cancelled || tokenRef.current !== myToken) return;
        const list = Array.isArray(res.data) ? res.data : [];
        if (list.length > 0) {
          const sorted = sortConvs(list);
          setConversations(sorted);
          setActiveId(sorted[0].id);
        } else {
          const conv = makeFreshConv();
          setConversations([conv]);
          setActiveId(conv.id);
          persist(conv);
        }
      } catch {
        if (cancelled || tokenRef.current !== myToken) return;
        const conv = makeFreshConv();
        setConversations([conv]);
        setActiveId(conv.id);
      } finally {
        if (!cancelled) setLoadingList(false);
      }
    }

    if (token) {
      loadForLogin();
    } else {
      const conv = makeFreshConv();
      setConversations([conv]);
      setActiveId(conv.id);
    }

    return () => { cancelled = true; };
  }, [token]);

  const newConversation = () => {
    const conv = makeFreshConv();
    if (token) {
      setConversations(list => [conv, ...list]);
      persist(conv);
    } else {
      setConversations([conv]);
    }
    setActiveId(conv.id);
    return conv.id;
  };

  // 進入農藥博士頁面時呼叫：直接停留在目前的對話，不自動開新的。
  const enterRAG = () => {
    if (activeId && conversations.some(c => c.id === activeId)) {
      return activeId;
    }
    const sorted = sortConvs(conversations);
    if (sorted.length > 0) {
      setActiveId(sorted[0].id);
      return sorted[0].id;
    }
    return newConversation();
  };

  const deleteConversation = (id) => {
    if (token) deleteConversationApi(id).catch(() => {});
    setConversations(list => {
      const remaining = list.filter(c => c.id !== id);
      if (remaining.length === 0) {
        const conv = makeFreshConv();
        setActiveId(conv.id);
        if (token) persist(conv);
        return [conv];
      }
      if (activeId === id) setActiveId(sortConvs(remaining)[0].id);
      return remaining;
    });
  };

  const renameConversation = (id, newTitle) => {
    const title = (newTitle || '').trim() || '未命名對話';
    setConversations(list => list.map(c => c.id === id ? { ...c, title } : c));
    if (token) renameConversationApi(id, title).catch(() => {});
  };

  const togglePin = (id) => {
    let nextPinned = false;
    setConversations(list => list.map(c => {
      if (c.id === id) { nextPinned = !c.pinned; return { ...c, pinned: nextPinned }; }
      return c;
    }));
    if (token) pinConversationApi(id, nextPinned).catch(() => {});
  };

  // 送出問題並取得回答。
  // 關鍵修正：回答回來後「先組好完整對話物件、await 存進後端、再更新畫面」。
  // 這樣即使使用者馬上登出，回答也已經確定寫入後端，不會出現「問題在、回答不見」的情況。
  const send = async (convId, question) => {
    const qtext = question.trim();
    if (!qtext) return;

    // 先在畫面寫入使用者訊息，並記下這筆對話當下的完整內容
    let baseConv = null;
    setConversations(list => list.map(c => {
      if (c.id !== convId) return c;
      const updated = {
        ...c,
        title: !c.messages.some(m => m.role === 'user') ? makeTitle(qtext) : c.title,
        messages: [...c.messages, { role: 'user', text: qtext }],
        updatedAt: Date.now(),
      };
      baseConv = updated;
      return updated;
    }));

    setPendingIds(p => [...p, convId]);
    try {
      const FOLLOWUP_WORDS = ['還有', '其他', '別的', '它', '牠', '那個', '這個', '再', '更多', '呢'];
      const isFollowup = FOLLOWUP_WORDS.some(w => qtext.includes(w));
      const res = await askQuestion(qtext, lastCropRef.current, lastPestRef.current, isFollowup);
      const { answer, sources, isRefusal, crop, pest } = res.data;
      if (crop) lastCropRef.current = crop;
      if (pest) lastPestRef.current = pest;
      else if (!isFollowup) lastPestRef.current = null;
      const finalConv = baseConv ? {
        ...baseConv,
        messages: [...baseConv.messages, { role: 'bot', text: answer, sources: sources || [], isRefusal }],
        updatedAt: Date.now(),
      } : null;

      // 先確保寫進後端（await），再更新畫面。即使緊接著登出，資料也已落地。
      if (finalConv) await persist(finalConv);

      setConversations(list => list.map(c => c.id === convId && finalConv ? finalConv : c));
    } catch {
      const errConv = baseConv ? {
        ...baseConv,
        messages: [...baseConv.messages, { role: 'bot', text: '連線失敗，請確認後端是否正在運行。', sources: [], isRefusal: true }],
      } : null;
      if (errConv) await persist(errConv);
      setConversations(list => list.map(c => c.id === convId && errConv ? errConv : c));
    } finally {
      setPendingIds(p => p.filter(id => id !== convId));
    }
  };

  return (
    <ChatContext.Provider value={{
      conversations, activeId, setActiveId, pendingIds, loadingList,
      newConversation, enterRAG, deleteConversation, renameConversation, togglePin, send,
      isGuest: !token,
    }}>
      {children}
    </ChatContext.Provider>
  );
}

export function useChat() {
  return useContext(ChatContext);
}