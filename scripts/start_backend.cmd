@echo off
cd /d E:\my_project\Teacher_AICoding
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
E:\my_project\LX_AICoding\.venv\Scripts\python.exe -m uvicorn agent.app:app --host 127.0.0.1 --port 2024
