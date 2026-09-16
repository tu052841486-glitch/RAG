# 農藥知識問答系統

## 啟動步驟

### 後端
```bash
cd pesticide-backend
pip install -r requirements.txt
# 建立 .env 填入 API key
cp .env.example .env

# 第一次執行（向量化，約3-5分鐘）
python build_index.py

# 啟動後端
uvicorn main:app --reload --port 8000
```
https://pesticide-doctor-api.fly.dev/dashboard

後台：http://localhost:8000/dashboard

### 前端
```bash
cd pesticide-frontend
npm install
npm start
```

前台：http://localhost:3000

https://pesticide-doctor-frontend.vercel.app/
