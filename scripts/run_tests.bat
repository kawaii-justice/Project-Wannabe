@echo off
setlocal
cd /d "%~dp0.."
set "NO_PAUSE=0"
if /I "%~1"=="/nopause" set "NO_PAUSE=1"

if exist "venv\Scripts\python.exe" (
    call "venv\Scripts\activate.bat"
) else (
    call "%~dp0setup_venv.bat" /nopause
    if errorlevel 1 (
        if "%NO_PAUSE%"=="0" pause
        exit /b 1
    )
    call "venv\Scripts\activate.bat"
)

set QT_QPA_PLATFORM=offscreen
python -m unittest discover -s tests
if errorlevel 1 (
    if "%NO_PAUSE%"=="0" pause
    exit /b 1
)
if "%NO_PAUSE%"=="0" pause
