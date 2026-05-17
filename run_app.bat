@echo off
setlocal
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    call "venv\Scripts\activate.bat"
    python main.py
) else (
    call "%~dp0scripts\setup_venv.bat" /run /nopause
)
if errorlevel 1 pause
