import React, { useEffect, useState } from 'react';
import { generateQuiz } from '../services/api';
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

export default function Teach() {
  const [cards, setCards] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [flipped, setFlipped] = useState({});
  const [done, setDone] = useState({});
  const isMobile = useIsMobile();

  const loadCards = () => {
    setLoading(true);
    setError('');
    setFlipped({});
    setDone({});
    generateQuiz('term', 8)
      .then(r => {
        const qs = r.data['題目'] || [];
        const mapped = qs.map(q => ({ q: q.question, a: q.options[q.answer_index] }));
        setCards(mapped);
        if (mapped.length === 0) setError('目前資料不足以產生教材，請稍後再試。');
      })
      .catch(() => setError('教材載入失敗，請確認後端是否正在運行。'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadCards(); }, []);

  const flip = (i) => {
    setFlipped(f => ({ ...f, [i]: !f[i] }));
    if (!flipped[i]) setDone(d => ({ ...d, [i]: true }));
  };

  const progress = Object.keys(done).length;

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: isMobile ? '24px 16px' : '32px 24px', background: EARTH.page }}>
      <h1 style={{ fontSize: isMobile ? 26 : 32, fontWeight: 700, color: EARTH.accent, marginBottom: 9 }}>學習教材</h1>
      <div style={{ display: 'flex', alignItems: 'center', gap: isMobile ? 10 : 18, marginBottom: isMobile ? 22 : 31, flexWrap: 'wrap' }}>
        <p style={{ color: EARTH.textMuted, fontSize: isMobile ? 14 : 17, flex: isMobile ? '1 1 100%' : '0 1 auto' }}>點擊卡片翻面查看答案，每次重新整理都會換一批題目</p>
        <span style={{ marginLeft: isMobile ? 0 : 'auto', background: EARTH.accentLight, color: EARTH.accent, fontSize: 15, fontWeight: 500, padding: '5px 17px', borderRadius: 22 }}>
          進度 {progress} / {cards.length}
        </span>
        <button onClick={loadCards} style={{ background: 'none', border: `1px solid ${EARTH.border}`, color: EARTH.textMuted, fontSize: 14, padding: '6px 14px', borderRadius: 7, cursor: 'pointer' }}>
          🔄 換一批題目
        </button>
      </div>

      <div style={{ background: EARTH.accentLight, borderRadius: 8, height: 6, marginBottom: 28, overflow: 'hidden' }}>
        <div style={{ background: EARTH.accent, height: '100%', width: cards.length ? `${(progress / cards.length) * 100}%` : '0%', transition: 'width 0.4s' }} />
      </div>

      {loading ? <p style={{ color: EARTH.textMuted }}>載入中...</p> : error ? <p style={{ color: '#B23A3A' }}>{error}</p> : (
        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(auto-fill,minmax(310px,1fr))', gap: isMobile ? 16 : 22 }}>
          {cards.map((c, i) => (
            <div key={i} onClick={() => flip(i)} style={{ height: 184, cursor: 'pointer', perspective: 1000 }}>
              <div style={{ position: 'relative', width: '100%', height: '100%', transformStyle: 'preserve-3d', transition: 'transform 0.5s', transform: flipped[i] ? 'rotateY(180deg)' : 'none' }}>
                <div style={{ position: 'absolute', inset: 0, backfaceVisibility: 'hidden', background: done[i] ? EARTH.accentLight : EARTH.surface, border: `1.5px solid ${done[i] ? EARTH.accent : EARTH.border}`, borderRadius: 14, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 23, textAlign: 'center' }}>
                  <div style={{ fontSize: 28, marginBottom: 11 }}>❓</div>
                  <p style={{ fontWeight: 600, color: EARTH.textDark, fontSize: 18, lineHeight: 1.55 }}>{c.q}</p>
                  {done[i] && <span style={{ position: 'absolute', top: 11, right: 11, fontSize: 18 }}>✅</span>}
                </div>
                <div style={{ position: 'absolute', inset: 0, backfaceVisibility: 'hidden', transform: 'rotateY(180deg)', background: EARTH.accent, borderRadius: 14, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 23, textAlign: 'center' }}>
                  <div style={{ fontSize: 23, marginBottom: 9 }}>💡</div>
                  <p style={{ color: EARTH.accentText, fontSize: 17, lineHeight: 1.75 }}>{c.a}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}