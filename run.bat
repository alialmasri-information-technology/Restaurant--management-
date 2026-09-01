@echo off
REM Double-click this to start RE4 from source.
setlocal
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo Python was not found on your PATH.
    echo Install it from https://www.python.org/downloads/ and tick
    echo "Add Python to PATH" during setup.
    pause
    exit /b 1
)

python -c "import customtkinter, reportlab" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies, one moment...
    pip install -r requirements.txt || (
        echo Could not install the dependencies. Check your internet connection.
        pause
        exit /b 1
    )
)

python main.py %*
if errorlevel 1 pause
