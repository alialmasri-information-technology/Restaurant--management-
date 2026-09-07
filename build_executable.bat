@echo off
setlocal enabledelayedexpansion
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
REM Upgrading pip is a convenience, not a requirement: an offline machine with
REM a working pip should still build. Its failure is reported, not fatal.
python -m pip install --upgrade pip >nul 2>&1
if errorlevel 1 echo   (could not upgrade pip - carrying on with the one installed)
pip install -r requirements.txt pyinstaller || goto :failed

echo.
echo Building RE4.exe ...
pyinstaller --noconfirm --clean RE4.spec || goto :failed
if not exist "dist\RE4.exe" (
    echo PyInstaller reported success but dist\RE4.exe is not there.
    goto :failed
)

REM ---------------------------------------------------------------------
REM The installer. Inno Setup is optional - the bare exe is a complete
REM deliverable without it - but "optional" must never mean "silent". This
REM step used to probe the PATH alone and skip without a word if ISCC was
REM not on it, then print "Build complete" as though a release had been
REM made. A missing installer now says so in as many words, and an installer
REM that fails to compile stops the build instead of being passed over.
REM ---------------------------------------------------------------------
echo.
echo Building the installer...
set "ISCC="
for /f "delims=" %%I in ('where iscc 2^>nul') do if not defined ISCC set "ISCC=%%I"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"

set "BUILT_INSTALLER=no"
if not defined ISCC (
    echo.
    echo   ** The installer was NOT built. **
    echo   Inno Setup was not found on the PATH or in Program Files.
    echo   dist\RE4.exe is finished and works on its own; only the
    echo   one-click installer is missing.
    echo   To build it, install Inno Setup 6 from
    echo   https://jrsoftware.org/isdl.php and run this file again.
) else (
    echo Using "!ISCC!"
    "!ISCC!" installer.iss || goto :installer_failed
    set "BUILT_INSTALLER=yes"
)

echo.
echo ===================================================
echo  Build complete: dist\RE4.exe
if "!BUILT_INSTALLER!"=="yes" echo  Installer:      dist\installer\
if "!BUILT_INSTALLER!"=="no"  echo  Installer:      NOT built - see the note above
echo ===================================================
echo.
echo Copy dist\RE4.exe anywhere you like. On first run it creates
echo re4.db and a receipts folder beside itself, so put it in a
echo folder you can write to (not Program Files).
echo.
echo For a shop that would rather click an installer, the setup program
echo in dist\installer installs to Program Files and keeps the data in
echo %LOCALAPPDATA%\RE4.
echo.
echo First sign-in:  admin / admin123
echo RE4 will ask you to choose a real password straight away.
echo.
pause
exit /b 0

:installer_failed
echo.
echo Inno Setup could not compile installer.iss - see the messages above.
echo dist\RE4.exe was built and is usable; the installer was not.
echo.
pause
exit /b 1

:failed
echo.
echo Build failed - see the messages above.
echo.
pause
exit /b 1
