@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM 현재 폴더의 .env 설정을 읽고 GPU 모델을 한 번만 로드합니다.
python -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8000 --workers 1
pause
