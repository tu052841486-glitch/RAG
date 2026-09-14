import React, { useEffect, useState, useMemo, useRef } from 'react';
import { getPesticides, getCrops } from '../services/api';
import useIsMobile from '../hooks/useIsMobile';

const EARTH = {
  page: '#F5FAF7',
  surface: '#FFFFFF',
  border: '#CFE0D5',
  accent: '#0F4A34',
  accentLight: '#DCEAE2',
  accentText: '#E6F1EC',
  textDark: '#16241C',
  textMuted: '#3D4A43',
};

// 依「病蟲害名稱」推斷藥劑類型。
// 台灣農藥登記資料沒有獨立的「藥劑類型」欄位，但病蟲害名稱的用字有規律。
// 策略：先把「病害 / 雜草 / 蟎類」這三類判斷做紮實（用字較固定、好辨識），
// 其餘只要是在防治某種生物、且沒對到上述三類的，絕大多數就是蟲害，
// 因此 fallback 預設為殺蟲劑，而非籠統的「其他」，可大幅減少誤標。
// 只有完全無法辨識（空值等）才會落到「其他」。
function inferType(pestName) {
  const n = pestName || '';
  if (!n) return '其他';
  // 蟎類（紅蜘蛛、葉蟎、銹蟎等）
  if (/(蟎|螨|紅蜘蛛|葉蟎|銹蟎|赤蟎|茶細蟎)/.test(n)) return '殺蟎';
  // 病害（各種病名、病原）
  if (/(病|菌|疫|露|銹|銹病|炭疽|白粉|灰黴|黴|萎凋|立枯|猝倒|軟腐|潰瘍|瘡痂|銹斑|葉斑|黑斑|褐斑|輪斑|角斑|腐爛|腐病|枯萎|線蟲|病毒|毒素病)/.test(n)) return '殺菌';
  // 雜草
  if (/(草|莎草|稗)/.test(n)) return '除草';
  // 其餘防治對象視為蟲害（蚜、蟬、椿象、薊馬、飛蝨、粉蝨、蛾、蠅、甲蟲、毛蟲等）
  return '殺蟲';
}

export default function PesticideCards() {
  const [data, setData] = useState([]);
  const [crops, setCrops] = useState([]);
  const [crop, setCrop] = useState('');
  const [type, setType] = useState('');
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const isMobile = useIsMobile();

  // 作物搜尋（取代原本的下拉選單）
  const [cropQuery, setCropQuery] = useState('');
  const [cropOpen, setCropOpen] = useState(false);
  const cropBoxRef = useRef(null);

  useEffect(() => {
    getCrops().then(r => setCrops(r.data.作物清單 || []));
  }, []);

  useEffect(() => {
    setLoading(true);
    // 只依作物向後端拿資料，type（殺蟲/殺菌…）改在前端用 inferType 篩選，
    // 這樣「篩選邏輯」和「卡片顯示的類型」用同一套判斷，不會出現篩選 0 筆的問題。
    getPesticides({ crop: crop || undefined, limit: 200 })
      .then(r => setData(r.data)).finally(() => setLoading(false));
  }, [crop]);

  // 點作物搜尋框以外的地方就收起清單
  useEffect(() => {
    if (!cropOpen) return;
    const onDoc = (e) => { if (cropBoxRef.current && !cropBoxRef.current.contains(e.target)) setCropOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [cropOpen]);

  // 依輸入關鍵字篩選作物清單（最多顯示 60 筆，避免清單過長）
  const filteredCrops = useMemo(() => {
    const q = cropQuery.trim();
    if (!q) return crops.slice(0, 60);
    return crops.filter(c => c.includes(q)).slice(0, 60);
  }, [crops, cropQuery]);

  const pickCrop = (c) => {
    setCrop(c);
    setCropQuery(c);
    setCropOpen(false);
  };
  const clearCrop = () => {
    setCrop('');
    setCropQuery('');
    setCropOpen(false);
  };

  const typeColors = { '殺菌': '#DCEAE2', '殺蟲': '#C6DDCF', '除草': '#AFCFBC', '殺蟎': '#D0E3D8', '其他': '#EAF3EE' };

  const truncate = (s, n) => (s && s.length > n ? s.slice(0, n) + '…' : s);

  const FIELD_META = [
    { key: '作物名稱', label: '作物名稱' },
    { key: '病蟲害名稱', label: '防治對象' },
    { key: '登記分類名稱', label: '登記分類' },
    { key: '劑型', label: '劑型' },
    { key: '農藥含量', label: '農藥含量', hint: '有效成分濃度' },
    {
      key: '稀釋倍數', label: '稀釋倍數', unit: '倍',
      hint: (v) => {
        const n = parseFloat(v);
        if (!isNaN(n) && n > 1) {
          return `以此為例：農藥原液 1 毫升，加清水約 ${Math.round(n - 1)} 毫升，配成約 ${Math.round(n)} 毫升藥液使用`;
        }
        return '加水稀釋的比例，數字愈大代表加的水愈多、藥液愈淡';
      },
    },
    { key: '每公頃每次用量', label: '每公頃用量' },
    { key: '使用時期', label: '使用時期' },
    { key: '安全採收期_天', label: '安全採收期', hint: '最後一次施藥後，需等待幾天才能採收食用', unit: '天' },
    { key: '施藥間隔', label: '施藥間隔', hint: '兩次施藥之間至少要間隔幾天', unit: '天' },
    { key: '施用次數', label: '施用次數', hint: '整個生長季最多可施用幾次' },
    { key: '施用方法', label: '施用方法' },
    { key: '注意事項', label: '注意事項' },
  ];

  const selInput = { padding: '10px 16px', border: `1.5px solid ${EARTH.border}`, borderRadius: 8, background: EARTH.surface, color: EARTH.textDark, fontSize: 16, outline: 'none' };

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: isMobile ? '24px 16px' : '32px 24px', background: EARTH.page }}>
      <h1 style={{ fontSize: isMobile ? 26 : 32, fontWeight: 700, color: EARTH.accent, marginBottom: 9 }}>農藥百科卡</h1>
      <p style={{ color: EARTH.textMuted, marginBottom: isMobile ? 22 : 29, fontSize: isMobile ? 14 : 17 }}>查詢各作物可用農藥登記資料，含使用注意事項</p>

      <div style={{ display: 'flex', gap: 12, marginBottom: 28, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {/* 作物：可打字搜尋 */}
        <div ref={cropBoxRef} style={{ position: 'relative', flex: isMobile ? '1 1 100%' : '0 0 260px' }}>
          <div style={{ position: 'relative' }}>
            <input
              value={cropQuery}
              onChange={e => { setCropQuery(e.target.value); setCropOpen(true); }}
              onFocus={() => setCropOpen(true)}
              placeholder="輸入或選擇作物（例如：芒果）"
              style={{ ...selInput, width: '100%', boxSizing: 'border-box', paddingRight: 34 }}
            />
            {cropQuery && (
              <span onClick={clearCrop} title="清除"
                style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', cursor: 'pointer', color: EARTH.textMuted, fontSize: 16 }}>
                ✕
              </span>
            )}
          </div>
          {cropOpen && (
            <div style={{ position: 'absolute', top: '110%', left: 0, right: 0, zIndex: 30, background: EARTH.surface, border: `1px solid ${EARTH.border}`, borderRadius: 10, boxShadow: '0 6px 18px rgba(0,0,0,0.12)', maxHeight: 280, overflowY: 'auto' }}>
              <div onClick={clearCrop}
                style={{ padding: '10px 14px', fontSize: 15, cursor: 'pointer', color: EARTH.accent, borderBottom: `1px solid ${EARTH.accentLight}`, fontWeight: 600 }}
                onMouseEnter={e => e.currentTarget.style.background = EARTH.accentLight}
                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                全部作物
              </div>
              {filteredCrops.length === 0 ? (
                <div style={{ padding: '12px 14px', fontSize: 14, color: EARTH.textMuted }}>找不到「{cropQuery}」</div>
              ) : filteredCrops.map(c => (
                <div key={c} onClick={() => pickCrop(c)}
                  style={{ padding: '10px 14px', fontSize: 15, cursor: 'pointer', color: EARTH.textDark, background: c === crop ? EARTH.accentLight : 'transparent' }}
                  onMouseEnter={e => e.currentTarget.style.background = EARTH.accentLight}
                  onMouseLeave={e => e.currentTarget.style.background = c === crop ? EARTH.accentLight : 'transparent'}>
                  {c}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 類型：維持下拉（只有五個選項，不需搜尋）*/}
        <select value={type} onChange={e => setType(e.target.value)} style={{ ...selInput, flex: isMobile ? '1 1 100%' : '0 0 auto' }}>
          <option value="">全部類型</option>
          {['殺菌', '殺蟲', '除草', '殺蟎'].map(t => <option key={t} value={t}>{t}劑</option>)}
        </select>

        <span style={{ color: EARTH.textMuted, fontSize: 15, alignSelf: 'center' }}>共 {data.filter(p => !type || inferType(p.病蟲害名稱) === type).length} 筆</span>
      </div>

      {loading ? <p style={{ color: EARTH.textMuted }}>載入中...</p> : (
        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(auto-fill,minmax(260px,1fr))', gap: 16 }}>
          {data.filter(p => !type || inferType(p.病蟲害名稱) === type).map(p => {
            const t = inferType(p.病蟲害名稱);
            return (
              <div key={p.id} onClick={() => setSelected(p)} style={{ background: typeColors[t] || EARTH.surface, border: `1.5px solid ${EARTH.border}`, borderRadius: 12, padding: '18px 20px', cursor: 'pointer', transition: 'all 0.15s' }}
                onMouseEnter={e => { if (!isMobile) { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 4px 14px rgba(15,74,52,0.18)'; } }}
                onMouseLeave={e => { e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = 'none'; }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 11 }}>
                  <span style={{ background: EARTH.accent, color: EARTH.accentText, fontSize: 13, fontWeight: 500, padding: '3px 9px', borderRadius: 5 }}>{p.作物名稱}</span>
                  <span style={{ fontSize: 13, color: EARTH.textMuted }}>{t}劑</span>
                </div>
                <h3 style={{ fontWeight: 600, fontSize: 19, color: EARTH.textDark, marginBottom: 7 }}>{p.農藥中文普通名稱}</h3>
                <p style={{ fontSize: 16, color: EARTH.textMuted, marginBottom: 10 }}>{p.病蟲害名稱}</p>
                <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap', marginBottom: p.注意事項 ? 9 : 0 }}>
                  {p.稀釋倍數 && <span style={{ fontSize: 13, background: EARTH.surface, border: `1px solid ${EARTH.border}`, padding: '3px 8px', borderRadius: 5 }}>加水稀釋 {p.稀釋倍數} 倍</span>}
                  {p.安全採收期_天 && p.安全採收期_天 !== '-' && <span style={{ fontSize: 13, background: EARTH.surface, border: `1px solid ${EARTH.border}`, padding: '3px 8px', borderRadius: 5 }}>施藥後 {p.安全採收期_天} 天才能採收</span>}
                </div>
                {p.注意事項 && (
                  <p style={{ fontSize: 13, color: EARTH.accent, background: EARTH.surface, border: `1px dashed ${EARTH.border}`, borderRadius: 7, padding: '6px 9px', margin: 0, lineHeight: 1.55 }}>
                    ⚠️ {truncate(p.注意事項, 34)}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {!loading && data.filter(p => !type || inferType(p.病蟲害名稱) === type).length === 0 && (
        <p style={{ color: EARTH.textMuted, marginTop: 8 }}>沒有符合條件的資料，換個作物或類型試試。</p>
      )}

      {selected && (
        <div onClick={() => setSelected(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(43,38,32,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200, padding: isMobile ? 14 : 24 }}>
          <div onClick={e => e.stopPropagation()} style={{ background: EARTH.surface, borderRadius: 17, padding: isMobile ? '24px 20px' : '35px', maxWidth: 580, width: '100%', maxHeight: '85vh', overflowY: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 22 }}>
              <h2 style={{ fontWeight: 700, color: EARTH.accent, fontSize: isMobile ? 22 : 25 }}>{selected.農藥中文普通名稱}</h2>
              <button onClick={() => setSelected(null)} style={{ background: 'none', border: 'none', fontSize: 25, color: EARTH.textMuted, cursor: 'pointer' }}>✕</button>
            </div>
            {FIELD_META.map(({ key, label, hint, unit }) => {
              const value = selected[key];
              if (!value || value === '-') return null;
              const displayValue = unit && !String(value).includes(unit) ? `${value} ${unit}` : value;
              const hintText = typeof hint === 'function' ? hint(value) : hint;
              return (
                <div key={key} style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: isMobile ? 4 : 17, padding: '12px 0', borderBottom: `1px solid ${EARTH.accentLight}` }}>
                  <span style={{ color: EARTH.textMuted, fontSize: 16, minWidth: isMobile ? 'auto' : 125 }}>
                    {label}
                    {hintText && <div style={{ fontSize: 13, color: EARTH.textMuted, opacity: 0.85, marginTop: 3, lineHeight: 1.55 }}>（{hintText}）</div>}
                  </span>
                  <span style={{ color: EARTH.textDark, fontSize: 16, flex: 1 }}>{displayValue}</span>
                </div>
              );
            })}
            <div style={{ marginTop: 18, fontSize: 14, color: EARTH.textMuted }}>資料來源：農藥資訊服務網 2026 版</div>
          </div>
        </div>
      )}
    </div>
  );
}