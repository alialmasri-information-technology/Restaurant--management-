@echo off
echo ===================================================
echo Restaurant Management System - Build Script
echo ===================================================
echo.
echo This script will build a standalone executable for the Restaurant Management System.
echo.

REM Check if Python is installed
python --version > nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not installed or not in PATH.
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo Python is installed. Proceeding with build...
echo.

REM Create a virtual environment
echo Creating a virtual environment...
python -m venv venv
call venv\Scripts\activate

REM Install required packages
echo.
echo Installing required packages...
pip install customtkinter tkcalendar bcrypt pyinstaller

REM Check if all packages were installed successfully
if %errorlevel% neq 0 (
    echo.
    echo Failed to install required packages.
    echo Please check your internet connection and try again.
    pause
    exit /b 1
)

echo.
echo Packages installed successfully.
echo.
echo Building the executable...

REM Run PyInstaller to build the executable
pyinstaller --noconfirm --onefile --windowed ^
    --add-data "restaurant_management.db;." ^
    --add-data "setup_database.py;." ^
    --hidden-import customtkinter ^
    --hidden-import bcrypt ^
    --hidden-import tkcalendar ^
    --hidden-import sqlite3 ^
    --hidden-import tkinter ^
    --hidden-import datetime ^
    --name "RestaurantManager" ^
    restaurant_app_single.py

if %errorlevel% neq 0 (
    echo.
    echo Failed to build the executable.
    echo Please check the error messages above and try again.
    pause
    exit /b 1
)

echo.
echo Build completed successfully!
echo.
echo The executable is located at: dist\RestaurantManager.exe
echo.
echo You can now run the application by double-clicking on the executable.
echo.
echo Default admin credentials:
echo Username: admin
echo Password: admin123
echo.
pause
