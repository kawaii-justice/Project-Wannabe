@echo off
setlocal
cd /d "%~dp0"
set "AUTO_RUN=0"
set "NO_PAUSE=0"

if /I "%~1"=="/run" set "AUTO_RUN=1"
if /I "%~1"=="/nopause" set "NO_PAUSE=1"
if /I "%~2"=="/run" set "AUTO_RUN=1"
if /I "%~2"=="/nopause" set "NO_PAUSE=1"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.9 or newer and try again.
    if "%NO_PAUSE%"=="0" pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        if "%NO_PAUSE%"=="0" pause
        exit /b 1
    )
)

call "venv\Scripts\activate.bat"
echo Installing dependencies...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo Failed to upgrade pip.
    if "%NO_PAUSE%"=="0" pause
    exit /b 1
)

python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    if "%NO_PAUSE%"=="0" pause
    exit /b 1
)

echo Setup complete.

if "%AUTO_RUN%"=="1" goto run_app
if "%NO_PAUSE%"=="1" exit /b 0

choice /C YN /M "Start Project Wannabe now"
if errorlevel 2 exit /b 0

:run_app
echo Starting Project Wannabe...
python main.py
if errorlevel 1 (
    echo Project Wannabe exited with an error.
    if "%NO_PAUSE%"=="0" pause
    exit /b 1
)
