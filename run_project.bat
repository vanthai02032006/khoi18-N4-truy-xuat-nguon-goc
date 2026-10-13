@echo off
chcp 65001 >nul
echo ========================================================
echo   KHỞI ĐỘNG HỆ THỐNG TRUY XUẤT NGUỒN GỐC NÔNG SẢN
echo ========================================================

echo [1/3] Đang khởi động Backend API (FastAPI)...
start "Backend API - Port 8000" cmd /k "cd /d %~dp0backend && uvicorn app.main:app --reload --port 8000"

echo [2/3] Đang khởi động Frontend (Web UI)...
start "Frontend UI - Port 5500" cmd /k "cd /d %~dp0frontend && python -m http.server 5500"

echo [3/3] Đang mở trình duyệt...
timeout /t 2 >nul
start http://127.0.0.1:5500

echo ========================================================
echo   HỆ THỐNG ĐÃ SẴN SÀNG!
echo   - Web App UI:   http://127.0.0.1:5500
echo   - Backend API:  http://127.0.0.1:8000
echo   - Swagger Docs: http://127.0.0.1:8000/docs
echo   - Tài khoản Admin:  admin / 123456
echo   - Tài khoản Farmer: farmer / 123456
echo ========================================================
pause
