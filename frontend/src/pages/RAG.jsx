import React, { useState, useRef, useEffect } from 'react';
import { useChat } from '../context/ChatContext';
import useIsMobile from '../hooks/useIsMobile';

const QUICK = ['空心菜可以噴加保扶嗎？', '高麗菜小菜蛾要用什麼藥？', '草莓可以用哪些殺菌劑？', '甜椒殺蟎劑推薦？'];

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
  return (text || '').replace(/\n{2,}/g, '\n');
}

// 前端壓縮照片：長邊縮到 maxSide，轉成 JPEG data URL（手機原圖動輒 5MB，上傳前先縮小）
function resizeImage(file, maxSide, quality) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('讀取照片失敗'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('無法讀取這張照片'));
      img.onload = () => {
        const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL('image/jpeg', quality));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

const withUnit = (v, unit) => (v ? (/^[\d,.\-~～]+$/.test(v) ? `${v} ${unit}` : v) : '—');

// 用藥合法性檢查結果
function ComplianceBanner({ c, isMobile }) {
  const ok = c.status === 'registered';
  return (
    <div role="status" style={{
      maxWidth: isMobile ? '96%' : '92%', marginBottom: 8,
      background: ok ? '#E3F2E8' : '#FBE4E1',
      borderLeft: `5px solid ${ok ? '#1E7A46' : '#B3261E'}`,
      borderRadius: 10, padding: isMobile ? '10px 13px' : '12px 16px',
      color: ok ? '#124A2B' : '#7A1712', fontSize: isMobile ? 15 : 16, lineHeight: 1.6,
    }}>
      <div style={{ fontWeight: 700, fontSize: isMobile ? 16 : 17, marginBottom: 2 }}>
        {ok ? `✅ 已登記：${c.pesticide} 可用於${c.crop_display}` : `⛔ 未登記：${c.pesticide} 不可用於${c.crop_display}`}
      </div>
      {ok ? (
        <div>登記防治對象：{c.pests.join('、')}</div>
      ) : (
        <div>
          農藥必須依登記的作物與病蟲害使用。
          {c.other_crops?.length > 0 && <>此藥目前登記於：{c.other_crops.join('、')}等作物。</>}
        </div>
      )}
    </div>
  );
}

// 用藥處方卡：所有數值直接取自官方登記資料，不經 AI 改寫
function PrescriptionCard({ card, isMobile }) {
  const [open, setOpen] = useState(false);
  const rows = [
    ['稀釋倍數', withUnit(card.dilution, '倍')],
    ['每公頃每次用量', card.dosage || '—'],
    ['使用時期', card.timing || '—'],
    ['施藥間隔', withUnit(card.interval, '天')],
    ['施用次數', card.times || '—'],
  ];
  const details = [
    ['含量／劑型', [card.content, card.formulation].filter(Boolean).join('／') || '—'],
    ['施用方法', card.method || '—'],
    ['注意事項', card.notes || '—'],
    ['登記核准日期', card.approved || '—'],
  ];
  const phi = card.phi && /^\d+$/.test(card.phi) ? card.phi : null;
  return (
    <div style={{ background: EARTH.surface, border: `1px solid ${EARTH.border}`, borderRadius: 12, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'stretch' }}>
        <div style={{ flex: 1, padding: isMobile ? '11px 13px' : '13px 16px', minWidth: 0 }}>
          <div style={{ fontSize: isMobile ? 17 : 18, fontWeight: 700, color: EARTH.textDark }}>{card.name}</div>
          <div style={{ fontSize: 13, color: EARTH.textMuted, marginBottom: 8 }}>防治：{card.pest || '—'}</div>
          {rows.map(([k, v]) => (
            <div key={k} style={{ display: 'flex', gap: 8, fontSize: isMobile ? 14 : 15, lineHeight: 1.6 }}>
              <span style={{ color: EARTH.textMuted, flexShrink: 0, width: isMobile ? 92 : 108 }}>{k}</span>
              <span style={{ color: EARTH.textDark }}>{v}</span>
            </div>
          ))}
        </div>
        <div style={{
          width: isMobile ? 78 : 92, flexShrink: 0, background: EARTH.accent, color: EARTH.accentText,
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '8px 4px', textAlign: 'center',
        }}>
          <div style={{ fontSize: 12, lineHeight: 1.3 }}>安全採收期</div>
          <div style={{ fontSize: phi ? (isMobile ? 30 : 34) : 18, fontWeight: 700, lineHeight: 1.15 }}>{phi || '—'}</div>
          {phi && <div style={{ fontSize: 12 }}>天</div>}
        </div>
      </div>
      <button onClick={() => setOpen(o => !o)} aria-expanded={open}
        style={{ width: '100%', border: 'none', borderTop: `1px solid ${EARTH.border}`, background: EARTH.accentSoft, color: EARTH.accent, padding: '8px 0', fontSize: 14, cursor: 'pointer' }}>
        {open ? '收起詳細用法' : '查看詳細用法與注意事項'}
      </button>
      {open && (
        <div style={{ padding: isMobile ? '10px 13px' : '12px 16px', borderTop: `1px solid ${EARTH.border}` }}>
          {details.map(([k, v]) => (
            <div key={k} style={{ fontSize: isMobile ? 14 : 15, lineHeight: 1.65, marginBottom: 6 }}>
              <div style={{ color: EARTH.textMuted, fontSize: 13 }}>{k}</div>
              <div style={{ color: EARTH.textDark }}>{v}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PrescriptionCards({ cards, isMobile }) {
  const [showAll, setShowAll] = useState(false);
  const list = showAll ? cards : cards.slice(0, 4);
  return (
    <div style={{ width: '100%', maxWidth: isMobile ? '96%' : '92%', marginTop: 10 }}>
      <div style={{ fontSize: isMobile ? 15 : 16, fontWeight: 700, color: EARTH.accent, marginBottom: 8 }}>
        📋 登記用藥處方卡（{cards.length} 種）
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(auto-fill, minmax(340px, 1fr))', gap: 10 }}>
        {list.map((c, i) => <PrescriptionCard key={`${c.name}-${i}`} card={c} isMobile={isMobile} />)}
      </div>
      {cards.length > 4 && (
        <button onClick={() => setShowAll(v => !v)}
          style={{ marginTop: 10, background: 'transparent', border: `1px solid ${EARTH.border}`, color: EARTH.accent, borderRadius: 20, padding: '6px 16px', fontSize: 14, cursor: 'pointer' }}>
          {showAll ? '只顯示前 4 種' : `顯示全部 ${cards.length} 種`}
        </button>
      )}
      <div style={{ fontSize: 12, color: EARTH.textMuted, marginTop: 8 }}>
        數值直接取自農藥資訊服務網登記資料，實際使用請以農藥標示為準。
      </div>
    </div>
  );
}

// 拍照問藥的辨識結果：使用者點選最符合的候選後，才送出用藥查詢
function IdentifyResult({ data, onPick, disabled, isMobile }) {
  const box = {
    maxWidth: isMobile ? '96%' : '92%', background: EARTH.surface, border: `1px solid ${EARTH.border}`,
    borderRadius: 14, padding: isMobile ? '12px 15px' : '14px 18px', color: EARTH.textDark,
    fontSize: isMobile ? 16 : 17, lineHeight: 1.65,
  };
  if (!data || !data.is_plant || !data.candidates?.length) {
    return (
      <div style={{ ...box, background: EARTH.warnBg, border: '1px solid #F0DBA0' }}>
        這張照片看不太出病蟲害徵狀。請靠近一點，讓受害的葉片、果實或蟲體占畫面大部分，光線充足再拍一次。
        {data?.tip && <div style={{ marginTop: 6 }}>{data.tip}</div>}
      </div>
    );
  }
  const cropLabel = data.crop || '';
  return (
    <div style={box}>
      {cropLabel && <div style={{ fontWeight: 700, marginBottom: 4 }}>作物：{cropLabel}</div>}
      {data.observation && <div style={{ marginBottom: 10 }}>{data.observation}</div>}
      <div style={{ fontSize: 14, color: EARTH.textMuted, marginBottom: 8 }}>
        AI 判斷可能是以下病蟲害，請點選最符合的一項，查詢合法登記用藥：
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {data.candidates.map((c, i) => {
          const pest = c.db_pest || c.name;
          const q = `${cropLabel}${pest}要用什麼藥？`;
          return (
            <button key={i} disabled={disabled} onClick={() => onPick(q)}
              style={{
                textAlign: 'left', background: i === 0 ? EARTH.accentLight : EARTH.accentSoft,
                border: `1px solid ${EARTH.border}`, borderRadius: 10, padding: '10px 14px',
                cursor: disabled ? 'default' : 'pointer', color: EARTH.textDark, fontSize: isMobile ? 15 : 16,
              }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontWeight: 700 }}>
                <span>{c.name}</span>
                <span style={{ color: EARTH.accent, flexShrink: 0 }}>{Math.round(c.confidence * 100)}%</span>
              </div>
              {c.reason && <div style={{ fontSize: 14, color: EARTH.textMuted, marginTop: 2 }}>{c.reason}</div>}
              <div style={{ fontSize: 13, color: EARTH.accent, marginTop: 4 }}>
                {c.registered_count > 0 ? `資料庫有 ${c.registered_count} 種登記用藥，點我查詢` : '點我查詢相關用藥'}
              </div>
            </button>
          );
        })}
      </div>
      {data.tip && <div style={{ fontSize: 14, color: EARTH.textMuted, marginTop: 10 }}>拍照小提醒：{data.tip}</div>}
      <div style={{ fontSize: 12, color: EARTH.textMuted, marginTop: 6 }}>影像辨識結果僅供參考，無法確定時請洽當地農會或植物醫師。</div>
    </div>
  );
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
    enterRAG, newConversation, deleteConversation, renameConversation, togglePin, send, sendImage, isGuest,
  } = useChat();
  const fileRef = useRef(null);
  const [photoError, setPhotoError] = useState('');
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

  const handlePhoto = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || loading) return;
    if (!file.type.startsWith('image/')) { setPhotoError('請選擇照片檔案'); return; }
    setPhotoError('');
    try {
      const [image, thumb] = await Promise.all([resizeImage(file, 1280, 0.85), resizeImage(file, 360, 0.7)]);
      sendImage(activeId, image, thumb);
    } catch (err) {
      setPhotoError(err.message || '照片處理失敗，請換一張再試');
    }
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
                    {m.image && (
                      <img src={m.image} alt="上傳的作物照片"
                        style={{ display: 'block', maxWidth: isMobile ? 200 : 260, width: '100%', borderRadius: 12, marginBottom: 8 }} />
                    )}
                    {m.text}
                  </div>
                ) : m.kind === 'identify' ? (
                  <IdentifyResult data={m.identify} onPick={handleSend} disabled={loading} isMobile={isMobile} />
                ) : (
                  <>
                  {m.compliance && m.compliance.map((c, k) => <ComplianceBanner key={k} c={c} isMobile={isMobile} />)}
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
                  {m.cards && m.cards.length > 0 && <PrescriptionCards cards={m.cards} isMobile={isMobile} />}
                  </>
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

          {photoError && <div role="alert" style={{ color: '#B3261E', fontSize: 14, marginBottom: 8 }}>{photoError}</div>}
          <div style={{ display: 'flex', gap: isMobile ? 8 : 12, paddingBottom: isMobile ? 16 : 26 }}>
            <input ref={fileRef} type="file" accept="image/*" onChange={handlePhoto} style={{ display: 'none' }} />
            <button onClick={() => fileRef.current?.click()} disabled={loading} title="拍照問藥" aria-label="拍照問藥"
              style={{
                width: isMobile ? 48 : 58, flexShrink: 0, borderRadius: 30, fontSize: isMobile ? 21 : 24,
                border: `1.5px solid ${EARTH.border}`, background: EARTH.surface, cursor: loading ? 'default' : 'pointer',
                opacity: loading ? 0.5 : 1,
              }}>
              📷
            </button>
            <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSend(input)}
              placeholder={isMobile ? "輸入問題或按 📷 拍照" : "輸入農藥問題，或按 📷 拍照辨識病蟲害"} disabled={loading}
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