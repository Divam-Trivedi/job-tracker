@echo off
REM build_windows.bat - Build Job Tracker for Windows

setlocal enabledelayedexpansion

set APP_NAME=Job Tracker
set APP_VERSION=1.0.0
set EXE_NAME=Job Tracker.exe
set SETUP_NAME=Job Tracker-1.0.0-setup.exe

echo.
echo ========================================
echo Building %APP_NAME% for Windows
echo ========================================

REM Step 1: Check Python
echo.
echo Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found
    echo Install from: https://www.python.org
    echo (Make sure to check "Add Python to PATH")
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo   Python: %PYTHON_VERSION%

REM Step 2: Create virtual environment
echo.
echo Setting up Python environment...
if not exist "venv" (
    python -m venv venv
    echo   Created virtual environment
) else (
    echo   Virtual environment already exists
)

call venv\Scripts\activate.bat

REM Step 3: Install dependencies
echo.
echo Installing Python dependencies...
python -m pip install --upgrade pip setuptools wheel >nul 2>&1
pip install ^
    pyinstaller ^
    flask ^
    requests ^
    google-auth-oauthlib ^
    google-auth-httplib2 ^
    google-api-python-client >nul 2>&1

echo   Installing optional LLM providers...
pip install google-generativeai openai anthropic >nul 2>&1

REM Step 4: Build with PyInstaller
echo.
echo Building with PyInstaller...
if exist "build" rmdir /s /q build >nul 2>&1
if exist "dist" rmdir /s /q dist >nul 2>&1

pyinstaller build.spec --clean --noconfirm >nul 2>&1

if not exist "dist\%EXE_NAME%" (
    echo ERROR: Build failed
    pause
    exit /b 1
)

echo   Created: dist\%EXE_NAME%

REM Step 5: Create NSIS installer (optional, requires NSIS installation)
echo.
echo Checking for NSIS installer...
set NSIS_PATH=
if exist "C:\Program Files\NSIS\makensis.exe" (
    set NSIS_PATH=C:\Program Files\NSIS\makensis.exe
) else if exist "C:\Program Files (x86)\NSIS\makensis.exe" (
    set NSIS_PATH=C:\Program Files (x86)\NSIS\makensis.exe
)

if not "%NSIS_PATH%"=="" (
    echo   Found NSIS at: %NSIS_PATH%
    echo   Building installer...
    REM This would create an NSIS installer
    REM For now, just create a simple folder
) else (
    echo   NSIS not found (optional)
)

REM Step 6: Create portable directory
echo.
echo Creating portable distribution...
mkdir dist\Job Tracker Portable >nul 2>&1
copy "dist\%EXE_NAME%" "dist\Job Tracker Portable\" >nul 2>&1
copy "README.md" "dist\Job Tracker Portable\README.txt" 2>nul || (
    echo # Job Tracker - Portable Version > "dist\Job Tracker Portable\README.txt"
    echo. >> "dist\Job Tracker Portable\README.txt"
    echo 1. Double-click "Job Tracker.exe" to launch >> "dist\Job Tracker Portable\README.txt"
    echo 2. Your default browser will open at http://localhost:8080 >> "dist\Job Tracker Portable\README.txt"
    echo 3. First run: Click "Allow" for Gmail OAuth >> "dist\Job Tracker Portable\README.txt"
    echo 4. Grant "Read and send emails" permissions >> "dist\Job Tracker Portable\README.txt"
    echo 5. token.json will be saved to: %%USERPROFILE%%\.job_tracker >> "dist\Job Tracker Portable\README.txt"
)

REM Step 7: Summary
echo.
echo ========================================
echo Build complete!
echo ========================================
echo.
echo Output:
echo   EXE:      dist\%EXE_NAME%
echo   Portable: dist\Job Tracker Portable\
echo.
echo To distribute:
echo   1. Zip: dist\Job Tracker Portable\
echo   2. Share the .zip file
echo   3. User extracts and runs Job Tracker.exe
echo.
echo First run:
echo   1. App opens browser to http://localhost:8080
echo   2. User clicks "Allow" for Gmail OAuth
echo   3. User grants "Read and send" permissions
echo   4. token.json saved to: %%USERPROFILE%%\.job_tracker
echo.
pause
