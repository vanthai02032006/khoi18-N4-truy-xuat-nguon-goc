@echo off
echo Dang khoi dong Backend va Frontend...

:: Mo terminal moi va chay Backend
start "Backend Server" cmd /k "cd backend && call .venv\Scripts\activate.bat && uvicorn app.main:app --reload"

:: Mo terminal moi va chay Frontend
start "Frontend Server" cmd /k "cd frontend && python -m http.server 8080"

:: Doi 3 giay de cac server khoi dong roi mo trinh duyet
timeout /t 3 >nobreak
start http://localhost:8080

echo Da khoi dong xong! Ban co the tat cua so nay.
pause
