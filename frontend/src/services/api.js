import axios from 'axios';
const API = axios.create({ baseURL: process.env.REACT_APP_API_URL || 'http://localhost:8000' });

// 每個請求自動帶上登入憑證（若已登入）
API.interceptors.request.use((config) => {
  const token = localStorage.getItem('auth_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// 只有「登入驗證類」的請求（/api/auth/me）回 401 時，才判定為 token 真的失效並登出。
// 其他一般 API（模擬考、教材、農藥卡、對話…）偶發 401（例如後端冷啟動、短暫異常）
// 不會把使用者踢回登入頁，避免明明登入卻被莫名登出的問題。
const AUTH_VERIFY_PATHS = ['/api/auth/me'];

API.interceptors.response.use(
  (res) => res,
  (err) => {
    const status = err.response?.status;
    const url = err.config?.url || '';
    const isAuthVerify = AUTH_VERIFY_PATHS.some(p => url.includes(p));
    if (status === 401 && isAuthVerify) {
      localStorage.removeItem('auth_token');
      localStorage.removeItem('auth_username');
      window.dispatchEvent(new Event('auth-expired'));
    }
    return Promise.reject(err);
  }
);

export const getPesticides = (params) => API.get('/api/pesticides', { params });
export const getCrops = () => API.get('/api/pesticides/crops');
export const searchPesticides = (q) => API.get('/api/pesticides/search', { params: { q } });
export const askQuestion = (question, prevCrop, prevPest, isFollowup) => API.post('/api/ask', { question, prev_crop: prevCrop || null, prev_pest: prevPest || null, is_followup: !!isFollowup });
export const getNews = () => API.get('/api/news');
export const generateQuiz = (category, count) => API.get('/api/quiz/generate', { params: { category, count } });

// 帳號登入
export const registerUser = (username, password) => API.post('/api/auth/register', { username, password });
export const loginUser = (username, password) => API.post('/api/auth/login', { username, password });
export const logoutUser = () => API.post('/api/auth/logout');
export const getMe = () => API.get('/api/auth/me');

// 對話紀錄（皆需登入；後端只會回傳/操作本人的對話）
export const listConversations = () => API.get('/api/conversations');
export const upsertConversation = (conv) => API.put(`/api/conversations/${conv.id}`, {
  id: conv.id,
  title: conv.title,
  messages: conv.messages,
  pinned: !!conv.pinned,
  updated_at: conv.updatedAt,
});
export const deleteConversationApi = (id) => API.delete(`/api/conversations/${id}`);
export const renameConversationApi = (id, title) => API.patch(`/api/conversations/${id}/rename`, { title });
export const pinConversationApi = (id, pinned) => API.patch(`/api/conversations/${id}/pin`, { pinned });