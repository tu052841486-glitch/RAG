import React, { createContext, useContext, useState, useEffect } from 'react';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem('auth_token') || null);
  const [username, setUsername] = useState(() => localStorage.getItem('auth_username') || null);

  const login = (tok, name) => {
    localStorage.setItem('auth_token', tok);
    localStorage.setItem('auth_username', name);
    setToken(tok);
    setUsername(name);
  };

  const logout = () => {
    localStorage.removeItem('auth_token');
    localStorage.removeItem('auth_username');
    setToken(null);
    setUsername(null);
  };

  // 當 api.js 偵測到 401（憑證失效）時會發出 'auth-expired' 事件，
  // 這裡收到後就更新登入狀態，讓畫面自然回到未登入樣貌，不需要強制跳頁。
  useEffect(() => {
    const onExpired = () => {
      setToken(null);
      setUsername(null);
    };
    window.addEventListener('auth-expired', onExpired);
    return () => window.removeEventListener('auth-expired', onExpired);
  }, []);

  return (
    <AuthContext.Provider value={{ token, username, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}