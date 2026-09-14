import React, { useState, useRef, useEffect } from 'react';
import { useChat } from '../context/ChatContext';
import useIsMobile from '../hooks/useIsMobile';

const QUICK = ['草莓可以用哪些殺菌劑？', '空心菜殺蟲劑有哪些？', '高麗菜除草劑怎麼用？', '甜椒殺螨劑推薦？'];

const EARTH = {
  page: '#F5FAF7',
  surface: '#FFFFFF',
  sidebarBg: '#EFF6F1',
  border: '#CFE0D5',
  accent: '#0F4A34',
  accentLight: '#DCEAE2',
  accentSoft: '#EAF3EE',
  accentText: '#E6F1EC',
  textDark: '#16241C',
  textMuted: '#3D4A43',
  warnBg: '#FCEFD4',
};

function tidyText(text) {
  return text.replace(/\n{2,}/g, '\n');
}

// 側邊欄單筆對話：含 ⋯ 選單（釘選 / 重新命名 / 刪除）、就地改名、刪除確認
function ConversationRow({ conv, active, onSelect, onRename, onTogglePin, onDelete }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [draft, setDraft] = useState(conv.title);
  const inputRef = useRef(null);
  const rowRef = useRef(null);

  useEffect(() => {
    if (editing) { inputRef.current?.focus(); inputRef.current?.select(); }
  }, [editing]);

  useEffect(() => {
    if (!menuOpen) return;
    const onDocClick = (e) => { if (rowRef.current && !rowRef.current.contains(e.target)) setMenuOpen(false); };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [menuOpen]);

  const commitRename = () => { onRename(conv.id, draft); setEditing(false); };

  return (
    <div ref={rowRef} style={{ position: 'relative' }}>
      <div onClick={() => !editing && onSelect(conv.id)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6,
          padding: '11px 11px', borderRadius: 9, cursor: 'pointer', fontSize: 14,
          background: active ? EARTH.accentLight : 'transparent',
          color: active ? EARTH.textDark : EARTH.textMuted,
          fontWeight: active ? 600 : 400,
        }}>
        {editing ? (
          <input
            ref={inputRef}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') { setDraft(conv.title); setEditing(false); } }}
            onBlur={commitRename}
            onClick={e => e.stopPropagation()}
            style={{ flex: 1, minWidth: 0, fontSize: 14, padding: '4px 6px', border: `1px solid ${EARTH.accent}`, borderRadius: 6, outline: 'none' }}
          />
        ) : (
          <span style={{ display: 'flex', alignItems: 'center', gap: 5, minWidth: 0 }}>
            {conv.pinned && <span title="已釘選" style={{ flexShrink: 0 }}>📌</span>}
            <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{conv.title}</span>
          </span>
        )}

        {!editing && (
          <span onClick={(e) => { e.stopPropagation(); setMenuOpen(o => !o); }} title="更多"
            style={{ opacity: 0.6, fontSize: 18, lineHeight: 1, padding: '4px 8px', borderRadius: 6, flexShrink: 0 }}>
            ⋯
          </span>
        )}
      </div>

      {menuOpen && !editing && (
        <div style={{
          position: 'absolute', top: 42, right: 8, zIndex: 20,
          background: EARTH.surface, border: `1px solid ${EARTH.border}`, borderRadius: 10,
          boxShadow: '0 6px 18px rgba(0,0,0,0.12)', overflow: 'hidden', minWidth: 140,
        }}>
          <MenuItem label={conv.pinned ? '📌 取消釘選' : '📌 釘選'}
            onClick={() => { onTogglePin(conv.id); setMenuOpen(false); }} />
          <MenuItem label="✏️ 重新命名"
            onClick={() => { setDraft(conv.title); setEditing(true); setMenuOpen(false); }} />
          <MenuItem label="🗑 刪除" danger
            onClick={() => { setConfirming(true); setMenuOpen(false); }} />
        </div>
      )}

      {confirming && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 300 }}
          onClick={() => setConfirming(false)}>
          <div onClick={e => e.stopPropagation()}
            style={{ background: EARTH.surface, borderRadius: 14, padding: '22px 24px', width: 320, maxWidth: '90%', boxShadow: '0 10px 30px rgba(0,0,0,0.2)' }}>
            <div style={{ fontSize: 16, fontWeight: 600, color: EARTH.textDark, marginBottom: 8 }}>刪除對話</div>
            <div style={{ fontSize: 14, color: EARTH.textMuted, marginBottom: 20, lineHeight: 1.6 }}>
              確定要刪除「{conv.title}」嗎？此動作無法復原。
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button onClick={() => setConfirming(false)}
                style={{ padding: '9px 18px', borderRadius: 8, border: `1px solid ${EARTH.border}`, background: EARTH.surface, color: EARTH.textMuted, fontSize: 14, cursor: 'pointer' }}>
                取消
              </button>
              <button onClick={() => { onDelete(conv.id); setConfirming(false); }}
                style={{ padding: '9px 18px', borderRadius: 8, border: 'none', background: '#C0392B', color: '#fff', fontSize: 14, fontWeight: 600, cursor: 'pointer' }}>
                刪除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function MenuItem({ label, onClick, danger }) {
  return (
    <div onClick={onClick}
      style={{ padding: '12px 15px', fontSize: 14, cursor: 'pointer', color: danger ? '#C0392B' : '#16241C', whiteSpace: 'nowrap' }}
      onMouseEnter={e => e.currentTarget.style.background = danger ? 'rgba(192,57,43,0.08)' : 'rgba(0,0,0,0.05)'}
      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
      {label}
    </div>
  );
}

// 側邊欄內容（桌機固定顯示、手機抽屜共用）
function SidebarContent({ conversations, activeId, onSelect, onNew, onRename, onTogglePin, onDelete }) {
  const sortedConvs = [...conversations].sort((a, b) => {
    if (!!b.pinned !== !!a.pinned) return (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0);
    return b.updatedAt - a.updatedAt;
  });
  return (
    <>
      <button onClick={onNew}
        style={{ width: '100%', padding: '12px 0', background: EARTH.accent, color: EARTH.accentText, border: 'none', borderRadius: 22, fontWeight: 600, fontSize: 15, cursor: 'pointer', marginBottom: 16 }}>
        ＋ 新對話
      </button>
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 3 }}>
        {sortedConvs.map(c => (
          <ConversationRow
            key={c.id}
            conv={c}
            active={c.id === activeId}
            onSelect={onSelect}
            onRename={onRename}
            onTogglePin={onTogglePin}
            onDelete={onDelete}
          />
        ))}
      </div>
    </>
  );
}

export default function RAG() {
  const {
    conversations, activeId, setActiveId, pendingIds,
    enterRAG, newConversation, deleteConversation, renameConversation, togglePin, send, isGuest,
  } = useChat();
  const [input, setInput] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const endRef = useRef(null);
  const enteredRef = useRef(false);
  const isMobile = useIsMobile();

  useEffect(() => {
    if (enteredRef.current) return;
    if (!activeId) enterRAG();
    enteredRef.current = true;

  }, [activeId]);

  const active = conversations.find(c => c.id === activeId) || conversations[0];
  const msgs = active ? active.messages : [];
  const loading = pendingIds.includes(activeId);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [msgs]);

  const handleSend = (q) => {
    if (!q.trim() || loading) return;
    const convId = activeId;
    setInput('');
    send(convId, q);
  };

  // 手機抽屜：選對話後自動收起
  const selectAndClose = (id) => { setActiveId(id); if (isMobile) setDrawerOpen(false); };
  const newAndClose = () => { newConversation(); if (isMobile) setDrawerOpen(false); };

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 64px)', background: EARTH.page, position: 'relative' }}>
      {/* 桌機版：固定側邊欄（僅登入者） */}
      {!isGuest && !isMobile && (
        <div style={{ width: 260, borderRight: `1px solid ${EARTH.border}`, background: EARTH.sidebarBg, display: 'flex', flexDirection: 'column', padding: '18px 14px' }}>
          <SidebarContent
            conversations={conversations} activeId={activeId}
            onSelect={setActiveId} onNew={newConversation}
            onRename={renameConversation} onTogglePin={togglePin} onDelete={deleteConversation}
          />
        </div>
      )}

      {/* 手機版：抽屜側邊欄（僅登入者） */}
      {!isGuest && isMobile && drawerOpen && (
        <>
          <div onClick={() => setDrawerOpen(false)}
            style={{ position: 'fixed', inset: 0, top: 64, background: 'rgba(0,0,0,0.4)', zIndex: 150 }} />
          <div style={{ position: 'fixed', top: 64, left: 0, bottom: 0, width: 268, maxWidth: '82%', background: EARTH.sidebarBg, borderRight: `1px solid ${EARTH.border}`, display: 'flex', flexDirection: 'column', padding: '18px 14px', zIndex: 151, boxShadow: '2px 0 16px rgba(0,0,0,0.18)' }}>
            <SidebarContent
              conversations={conversations} activeId={activeId}
              onSelect={selectAndClose} onNew={newAndClose}
              onRename={renameConversation} onTogglePin={togglePin} onDelete={deleteConversation}
            />
          </div>
        </>
      )}

      {/* 主對話區 */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ maxWidth: 1320, width: '100%', margin: '0 auto', padding: isMobile ? '16px 16px 0' : '30px 48px 0', flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

          {/* 手機版：開啟對話列表按鈕（僅登入者） */}
          {!isGuest && isMobile && (
            <button onClick={() => setDrawerOpen(true)}
              style={{ alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: 6, background: EARTH.surface, border: `1px solid ${EARTH.border}`, color: EARTH.accent, borderRadius: 20, padding: '7px 15px', fontSize: 14, fontWeight: 600, cursor: 'pointer', marginBottom: 12 }}>
              ☰ 對話紀錄
            </button>
          )}

          <h1 style={{ fontSize: isMobile ? 26 : 34, fontWeight: 700, color: EARTH.accent, marginBottom: 8 }}>🌿 農藥博士</h1>
          <p style={{ color: EARTH.textMuted, fontSize: isMobile ? 14 : 17, marginBottom: 12 }}>資料來源：農藥資訊服務網 2026 版 · 知識不足時建議洽詢官方管道</p>
          {isGuest && (
            <p style={{ color: EARTH.accent, background: EARTH.accentLight, fontSize: isMobile ? 13 : 14, padding: '8px 14px', borderRadius: 8, marginBottom: 12, lineHeight: 1.6 }}>
              目前為訪客模式，僅能單次問答、不會保留對話紀錄。註冊登入後可保存對話，並解鎖模擬考、農藥資訊卡、學習教材。
            </p>
          )}

          <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: isMobile ? 16 : 22, marginBottom: 18 }}>
            {msgs.map((m, i) => (
              <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
                {m.role === 'user' ? (
                  <div style={{
                    maxWidth: isMobile ? '88%' : '75%', background: EARTH.accent, color: EARTH.accentText,
                    borderRadius: '20px 20px 4px 20px', padding: isMobile ? '12px 15px' : '14px 18px', fontSize: isMobile ? 16 : 18, lineHeight: 1.75, whiteSpace: 'pre-wrap',
                  }}>
                    {m.text}
                  </div>
                ) : (
                  <div style={{
                    maxWidth: isMobile ? '96%' : '92%',
                    background: m.isRefusal ? EARTH.warnBg : EARTH.surface,
                    border: `1px solid ${m.isRefusal ? '#F0DBA0' : EARTH.border}`,
                    color: EARTH.textDark,
                    borderRadius: 14,
                    padding: isMobile ? '12px 15px' : '14px 18px',
                    fontSize: isMobile ? 16 : 18, lineHeight: 1.65, whiteSpace: 'pre-wrap',
                  }}>
                    {tidyText(m.text)}
                  </div>
                )}
                {m.sources && m.sources.length > 0 && (
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8, maxWidth: isMobile ? '96%' : '92%' }}>
                    {m.sources.slice(0, 4).map((s, j) => (
                      <a key={j} href={s.url} target="_blank" rel="noreferrer" style={{ fontSize: 13, background: EARTH.accentSoft, border: `1px solid ${EARTH.border}`, color: EARTH.accent, padding: '3px 10px', borderRadius: 6, textDecoration: 'none' }}>
                        📋{s.title?.slice(0, 20)}{s.date ? ` · ${s.date}` : ''}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {loading && (
              <div style={{ display: 'flex', alignItems: 'flex-start' }}>
                <div style={{ padding: '4px 0', display: 'flex', gap: 5 }}>
                  {[0, 1, 2].map(i => <span key={i} style={{ width: 9, height: 9, background: EARTH.accent, borderRadius: '50%', display: 'inline-block', animation: `bounce 1s ${i * 0.2}s infinite` }} />)}
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          <div style={{ display: 'flex', gap: isMobile ? 8 : 10, flexWrap: isMobile ? 'nowrap' : 'wrap', marginBottom: 14, overflowX: isMobile ? 'auto' : 'visible', paddingBottom: isMobile ? 4 : 0 }}>
            {QUICK.map((q, i) => (
              <button key={i} onClick={() => handleSend(q)} style={{ fontSize: isMobile ? 13 : 15, padding: isMobile ? '7px 13px' : '7px 17px', background: EARTH.surface, border: `1px solid ${EARTH.border}`, borderRadius: 24, color: EARTH.accent, whiteSpace: 'nowrap', flexShrink: 0 }}>
                {q}
              </button>
            ))}
          </div>

          <div style={{ display: 'flex', gap: isMobile ? 8 : 12, paddingBottom: isMobile ? 16 : 26 }}>
            <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSend(input)}
              placeholder="輸入農藥問題，按 Enter 送出…" disabled={loading}
              style={{ flex: 1, minWidth: 0, padding: isMobile ? '13px 16px' : '16px 20px', border: `1.5px solid ${EARTH.border}`, borderRadius: 30, fontSize: isMobile ? 16 : 18, outline: 'none', background: EARTH.surface }} />
            <button onClick={() => handleSend(input)} disabled={loading || !input.trim()}
              style={{ padding: isMobile ? '0 20px' : '0 28px', background: input.trim() && !loading ? EARTH.accent : EARTH.accentLight, color: input.trim() && !loading ? EARTH.accentText : EARTH.textMuted, border: 'none', borderRadius: 30, fontWeight: 600, fontSize: isMobile ? 16 : 18, flexShrink: 0 }}>
              送出
            </button>
          </div>
        </div>
      </div>
      <style>{`@keyframes bounce { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-6px)} }`}</style>
    </div>
  );
}