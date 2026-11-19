@echo off
rem 打包成单文件 exe
cd /d "%~dp0"
python -m pip install --quiet pyinstaller
python -m PyInstaller --noconfirm --onefile --noconsole --name stash-client ^
  --icon icon.ico --add-data "index.html;." app.py
if errorlevel 1 goto fail
echo.
echo 完成: dist\stash-client.exe
pause
exit /b 0
:fail
echo 打包失败
pause
exit /b 1
