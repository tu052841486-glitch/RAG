import React from 'react';
import { useNavigate } from 'react-router-dom';
import useIsMobile from '../hooks/useIsMobile';

export default function Home() {
  const nav = useNavigate();
  const isMobile = useIsMobile();

  const cards = [
    { icon: '🌿', title: '農藥資訊卡', desc: '查詢各作物可用農藥、稀釋倍數與安全採收期', path: '/cards', color: '#EAF3DE' },
    { icon: '📖', title: '學習教材', desc: '翻牌互動學習農藥基礎知識', path: '/teach', color: '#E8F0B0' },
    { icon: '🤖', title: 'AI 問答', desc: '用自然語言詢問農藥相關問題，AI 即時回答', path: '/rag', color: '#D4EDDA' },
    { icon: '📝', title: '模擬考', desc: '法規、植物保護、安全採收期選擇題自動出題，附錯題整理', path: '/quiz', color: '#FCE8B5' },
  ];

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: isMobile ? '28px 16px' : '40px 24px' }}>
      <div style={{ textAlign: 'center', marginBottom: isMobile ? 32 : 48 }}>
        <h1 style={{ fontSize: isMobile ? 26 : 32, fontWeight: 700, color: '#27500A', marginBottom: 12 }}>農藥知識問答系統</h1>
        <p style={{ color: '#6B7A52', fontSize: isMobile ? 14 : 16 }}>以 RAG 技術為核心，提供可溯源的農藥安全資訊</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(2, 1fr)', gap: isMobile ? 14 : 20 }}>
        {cards.map(c => (
          <div key={c.path} onClick={() => nav(c.path)} style={{ background: c.color, border: '1.5px solid #D4E088', borderRadius: 14, padding: isMobile ? '22px 20px' : '28px 24px', cursor: 'pointer', transition: 'transform 0.15s, box-shadow 0.15s' }}
            onMouseEnter={e => { if (!isMobile) { e.currentTarget.style.transform = 'translateY(-4px)'; e.currentTarget.style.boxShadow = '0 8px 24px rgba(39,80,10,0.12)'; } }}
            onMouseLeave={e => { e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = 'none'; }}>
            <div style={{ fontSize: isMobile ? 30 : 36, marginBottom: 14 }}>{c.icon}</div>
            <h3 style={{ fontWeight: 600, fontSize: isMobile ? 17 : 16, marginBottom: 8, color: '#27500A' }}>{c.title}</h3>
            <p style={{ color: '#6B7A52', fontSize: isMobile ? 14 : 13, lineHeight: 1.6 }}>{c.desc}</p>
          </div>
        ))}
      </div>
    </div>
  );
}