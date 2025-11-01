# Restaurant Management System - Simple Instructions

This document provides step-by-step instructions for running the Restaurant Management System on your Windows computer.

## Option 1: Run the Python Script Directly (Simplest)

1. **Install Python**:
   - Download Python from the official website: https://www.python.org/downloads/
   - Run the installer
   - **IMPORTANT**: Check the box that says "Add Python to PATH" during installation
   - Complete the installation

2. **Install Required Packages**:
   - Open Command Prompt (search for "cmd" in the Start menu)
   - Run these commands:
     ```
     pip install customtkinter tkcalendar bcrypt
     ```

3. **Run the Application**:
   - Simply double-click on `restaurant_app_single.py` to run the application
   - If that doesn't work, right-click on the file and select "Open with Python"
   - Alternatively, open Command Prompt, navigate to the folder containing the files, and run:
     ```
     python restaurant_app_single.py
     ```

## Option 2: Build a Standalone Executable

If you prefer a standalone .exe file that doesn't require Python:

1. **Double-click** on the `build_executable.bat` file
2. Wait for the process to complete (this may take a few minutes)
3. Once finished, find `RestaurantManager.exe` in the newly created `dist` folder
4. Double-click on `RestaurantManager.exe` to run the application

## Login Credentials

Use these default credentials to log in:
- **Admin User**:
  - Username: `admin`
  - Password: `admin123`

## Troubleshooting

If you encounter any issues:
- Make sure all files are in the same folder
- Try running the application directly with Python first to see any error messages
- If you get "module not found" errors, make sure you've installed all required packages

Enjoy using your Restaurant Management System!
