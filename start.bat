@echo off
rem 源码方式启动（需要 Python 3.10+）
cd /d "%~dp0"
start "" pythonw "%~dp0app.py"
timeout /t 2 >nul
start "" "http://127.0.0.1:8902/"
