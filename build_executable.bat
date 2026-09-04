@echo off
setlocal
echo ===================================================
echo  RE4 - Build a standalone Windows executable
echo ===================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo Python was not found on your PATH.
    echo Install it from https://www.python.org/downloads/ and tick
    echo "Add Python to PATH" during setup, then run this file again.
    echo.
    pause
    exit /b 1
)

if not exist venv (
    echo Creating a virtual environment...
    python -m venv venv || goto :failed
)

echo Installing dependencies...
call venv\Scripts\activate.bat || goto :failed
python -m pip install --upgrade pip >nul
pip install -r requirements.txt pyinstaller || goto :failed

echo.
echo Building RE4.exe ...
pyinstaller --noconfirm --clean RE4.spec || goto :failed

echo.
echo ===================================================
echo  Build complete: dist\RE4.exe
echo ===================================================
echo.
echo Copy dist\RE4.exe anywhere you like. On first run it creates
echo re4.db and a receipts folder beside itself, so put it in a
echo folder you can write to (not Program Files).
echo.
echo First sign-in:  admin / admin123
echo RE4 will ask you to choose a real password straight away.
echo.
pause
exit /b 0

:failed
echo.
echo Build failed - see the messages above.
echo.
pause
exit /b 1
