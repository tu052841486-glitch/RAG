import React, { useState, useEffect } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { logoutUser } from '../services/api';
import useIsMobile from '../hooks/useIsMobile';

const EARTH = {
  bg: '#F5FAF7',
  navBg: '#0F4A34',
  navText: '#E6F1EC',
  navActiveBg: 'rgba(255,255,255,0.18)',
  navActiveText: '#B7DDC7',
};

const NAV_LINKS = [['/', '農藥博士'], ['/cards', '農藥資訊'], ['/teach', '學習教材'], ['/quiz', '模擬考']];

export default function Navbar() {
  const { username, logout } = useAuth();
  const nav = useNavigate();
  const isMobile = useIsMobile();
  const [menuOpen, setMenuOpen] = useState(false);

  // 切回桌機寬度時自動收起手機選單，避免殘留展開狀態
  useEffect(() => { if (!isMobile) setMenuOpen(false); }, [isMobile]);

  const linkStyle = ({ isActive }) => ({
    color: isActive ? EARTH.navActiveText : 'rgba(255,255,255,0.75)',
    background: isActive ? EARTH.navActiveBg : 'transparent',
    fontSize: 18, fontWeight: 500, padding: '9px 18px', borderRadius: 8,
    transition: 'all 0.15s', textDecoration: 'none',
  });

  const handleLogout = async () => {
    try { await logoutUser(); } catch {}
    logout();
    setMenuOpen(false);
    nav('/');
  };

  return (
    <>
      <style>{`
        html, body, #root { background: ${EARTH.bg}; margin: 0; }
        body { font-family: -apple-system, 'Segoe UI', 'PingFang TC', 'Microsoft JhengHei', sans-serif; }
        * { box-sizing: border-box; }
      `}</style>

      <nav style={{ background: EARTH.navBg, padding: isMobile ? '0 18px' : '0 32px', height: 64, display: 'flex', alignItems: 'center', gap: isMobile ? 0 : 28, position: 'sticky', top: 0, zIndex: 100, boxShadow: '0 2px 12px rgba(15,74,52,0.28)' }}>
        <span style={{ color: '#B7DDC7', fontWeight: 700, fontSize: isMobile ? 20 : 24, marginRight: 11 }}>🌿 農藥博士</span>

        {/* 桌機版：橫向選單 */}
        {!isMobile && (
          <>
            {NAV_LINKS.map(([to, label]) => (
              <NavLink key={to} to={to} end={to === '/'} style={linkStyle}>{label}</NavLink>
            ))}
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 14 }}>
              {username ? (
                <>
                  <span style={{ color: 'rgba(255,255,255,0.85)', fontSize: 15 }}>👤 {username}</span>
                  <button onClick={handleLogout}
                    style={{ background: 'rgba(255,255,255,0.14)', color: '#E6F1EC', border: 'none', borderRadius: 8, padding: '8px 16px', fontSize: 14, fontWeight: 500, cursor: 'pointer' }}>
                    登出
                  </button>
                </>
              ) : (
                <NavLink to="/login"
                  style={{ background: 'rgba(255,255,255,0.16)', color: '#E6F1EC', border: 'none', borderRadius: 8, padding: '8px 18px', fontSize: 14, fontWeight: 600, textDecoration: 'none' }}>
                  登入 / 註冊
                </NavLink>
              )}
            </div>
          </>
        )}

        {/* 手機版：右側漢堡按鈕 */}
        {isMobile && (
          <button onClick={() => setMenuOpen(o => !o)} aria-label="選單"
            style={{ marginLeft: 'auto', background: 'transparent', border: 'none', color: '#E6F1EC', fontSize: 26, cursor: 'pointer', padding: '6px 8px', lineHeight: 1 }}>
            {menuOpen ? '✕' : '☰'}
          </button>
        )}
      </nav>

      {/* 手機版：展開的下拉選單 */}
      {isMobile && menuOpen && (
        <div style={{ position: 'sticky', top: 64, zIndex: 99, background: EARTH.navBg, boxShadow: '0 6px 16px rgba(15,74,52,0.32)', padding: '8px 14px 16px', display: 'flex', flexDirection: 'column', gap: 4 }}>
          {NAV_LINKS.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === '/'} onClick={() => setMenuOpen(false)}
              style={({ isActive }) => ({
                color: isActive ? EARTH.navActiveText : 'rgba(255,255,255,0.85)',
                background: isActive ? EARTH.navActiveBg : 'transparent',
                fontSize: 17, fontWeight: 500, padding: '13px 16px', borderRadius: 8, textDecoration: 'none',
              })}>
              {label}
            </NavLink>
          ))}
          <div style={{ height: 1, background: 'rgba(255,255,255,0.15)', margin: '6px 0' }} />
          {username ? (
            <>
              <span style={{ color: 'rgba(255,255,255,0.85)', fontSize: 15, padding: '6px 16px' }}>👤 {username}</span>
              <button onClick={handleLogout}
                style={{ background: 'rgba(255,255,255,0.14)', color: '#E6F1EC', border: 'none', borderRadius: 8, padding: '12px 16px', fontSize: 15, fontWeight: 500, cursor: 'pointer', textAlign: 'left' }}>
                登出
              </button>
            </>
          ) : (
            <NavLink to="/login" onClick={() => setMenuOpen(false)}
              style={{ background: 'rgba(255,255,255,0.16)', color: '#E6F1EC', borderRadius: 8, padding: '13px 16px', fontSize: 15, fontWeight: 600, textDecoration: 'none' }}>
              登入 / 註冊
            </NavLink>
          )}
        </div>
      )}
    </>
  );
}