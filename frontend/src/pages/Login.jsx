import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { loginUser, registerUser } from '../services/api';
import { useAuth } from '../context/AuthContext';
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

export default function Login() {
  const [mode, setMode] = useState('login'); // login | register
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [loading, setLoading] = useState(false);
  const auth = useAuth();
  const nav = useNavigate();
  const isMobile = useIsMobile();

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    setInfo('');
    if (!username.trim() || !password) {
      setError('請輸入帳號與密碼');
      return;
    }
    setLoading(true);
    try {
      if (mode === 'login') {
        const res = await loginUser(username.trim(), password);
        auth.login(res.data.token, res.data.username);
        nav('/');
      } else {
        await registerUser(username.trim(), password);
        setInfo('註冊成功，請登入');
        setMode('login');
        setPassword('');
      }
    } catch (err) {
      setError(err.response?.data?.detail || (mode === 'login' ? '登入失敗，請確認帳號密碼' : '註冊失敗'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: 'calc(100vh - 64px)', background: EARTH.page, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: isMobile ? 16 : 24 }}>
      <div style={{ width: '100%', maxWidth: 460, background: EARTH.surface, border: `1px solid ${EARTH.border}`, borderRadius: 20, padding: isMobile ? '34px 24px' : '46px 42px' }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div style={{ fontSize: 40, marginBottom: 10 }}>🌿</div>
          <h1 style={{ fontSize: 28, fontWeight: 700, color: EARTH.accent, marginBottom: 8 }}>農藥博士</h1>
          <p style={{ color: EARTH.textMuted, fontSize: 16 }}>{mode === 'login' ? '登入你的帳號' : '建立新帳號'}</p>
        </div>

        <div style={{ display: 'flex', background: EARTH.accentLight, borderRadius: 11, padding: 5, marginBottom: 28 }}>
          {[['login', '登入'], ['register', '註冊']].map(([m, label]) => (
            <button key={m} type="button" onClick={() => { setMode(m); setError(''); setInfo(''); }}
              style={{
                flex: 1, padding: '10px 0', borderRadius: 9, border: 'none', cursor: 'pointer',
                fontWeight: 600, fontSize: 16,
                background: mode === m ? EARTH.accent : 'transparent',
                color: mode === m ? EARTH.accentText : EARTH.textMuted,
                transition: 'all 0.15s',
              }}>
              {label}
            </button>
          ))}
        </div>

        <form onSubmit={submit}>
          <div style={{ marginBottom: 18 }}>
            <label style={{ display: 'block', fontSize: 15, color: EARTH.textMuted, marginBottom: 7 }}>帳號</label>
            <input value={username} onChange={e => setUsername(e.target.value)} autoFocus
              style={{ width: '100%', padding: '13px 16px', border: `1.5px solid ${EARTH.border}`, borderRadius: 11, fontSize: 16, outline: 'none', boxSizing: 'border-box' }} />
          </div>
          <div style={{ marginBottom: 10 }}>
            <label style={{ display: 'block', fontSize: 15, color: EARTH.textMuted, marginBottom: 7 }}>密碼</label>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)}
              style={{ width: '100%', padding: '13px 16px', border: `1.5px solid ${EARTH.border}`, borderRadius: 11, fontSize: 16, outline: 'none', boxSizing: 'border-box' }} />
          </div>

          {mode === 'register' && (
            <div style={{ fontSize: 13, color: EARTH.textMuted, marginBottom: 18, lineHeight: 1.6 }}>
              密碼需至少 6 碼，並包含大寫英文、小寫英文與數字。
            </div>
          )}
          {mode === 'login' && <div style={{ marginBottom: 12 }} />}

          {error && <div style={{ color: '#DC2626', fontSize: 15, marginBottom: 16 }}>{error}</div>}
          {info && <div style={{ color: EARTH.accent, fontSize: 15, marginBottom: 16 }}>{info}</div>}

          <button type="submit" disabled={loading}
            style={{ width: '100%', padding: '15px 0', background: EARTH.accent, color: EARTH.accentText, border: 'none', borderRadius: 11, fontWeight: 700, fontSize: 17, cursor: 'pointer' }}>
            {loading ? '處理中…' : mode === 'login' ? '登入' : '註冊帳號'}
          </button>
        </form>

        <div style={{ textAlign: 'center', marginTop: 22 }}>
          <Link to="/" style={{ color: EARTH.textMuted, fontSize: 14, textDecoration: 'none' }}>
            ← 先不登入，直接使用農藥博士（訪客模式）
          </Link>
        </div>
      </div>
    </div>
  );
}