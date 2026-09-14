import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Navbar from './components/Navbar';
import PesticideCards from './pages/PesticideCards';
import Teach from './pages/Teach';
import RAG from './pages/RAG';
import Quiz from './pages/Quiz';
import Login from './pages/Login';
import { AuthProvider, useAuth } from './context/AuthContext';
import { ChatProvider } from './context/ChatContext';

function RequireAuth({ children }) {
  const { token } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  return children;
}

// 農藥博士（RAG）+ 導覽列都在這層，不受 /login 影響
function Shell() {
  return (
    <>
      <Navbar />
      <Routes>
        {/* 農藥博士不需要登入，訪客可直接單次問答 */}
        <Route path="/" element={<RAG />} />
        <Route path="/rag" element={<RAG />} />
        {/* 模擬考、農藥資訊卡、學習教材，需要註冊登入才能使用 */}
        <Route path="/cards" element={<RequireAuth><PesticideCards /></RequireAuth>} />
        <Route path="/teach" element={<RequireAuth><Teach /></RequireAuth>} />
        <Route path="/quiz" element={<RequireAuth><Quiz /></RequireAuth>} />
      </Routes>
    </>
  );
}

function AppRoutes() {
  const { token } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={token ? <Navigate to="/" replace /> : <Login />} />
      <Route path="/*" element={<Shell />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        {/* ChatProvider 放在 Routes 之上，切頁不會讓農藥博士的對話狀態被卸載，
            背景還在生成的回答就算跳去別頁也能繼續完成並寫回原對話 */}
        <ChatProvider>
          <AppRoutes />
        </ChatProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}