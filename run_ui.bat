@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Run install_ui.bat first.
    exit /b 1
)
".venv\Scripts\python.exe" -m desktop_app
