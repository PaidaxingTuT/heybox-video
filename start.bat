@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%backend"

if not exist "%BACKEND_DIR%" (
    echo [ERROR] Backend directory not found: "%BACKEND_DIR%"
    pause
    exit /b 1
)

echo [INFO] Starting heybox-video backend...
echo [INFO] Backend dir: %BACKEND_DIR%
echo.

cd /d "%BACKEND_DIR%"

rem -- Try to find uv
set "UV_CMD="
for /f "delims=" %%i in ('where uv 2^>nul') do if not defined UV_CMD set "UV_CMD=%%i"

if defined UV_CMD (
    echo [INFO] Using uv: %UV_CMD%
    echo [INFO] Syncing dependencies...
    call "%UV_CMD%" sync
    if errorlevel 1 (
        echo [ERROR] uv sync failed with code %errorlevel%
        pause
        exit /b 1
    )
    echo [INFO] Starting server...
    "%UV_CMD%" run main.py
    echo.
    echo [INFO] Server stopped with exit code %errorlevel%
    pause
    exit /b 0
)

echo [INFO] uv not found, trying python...

rem -- Try to find Python
set "PYTHON_EXE="
for /f "delims=" %%i in ('where py 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
if not defined PYTHON_EXE (
    for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
)

if not defined PYTHON_EXE (
    echo [ERROR] Neither uv, py, nor python found on PATH.
    echo [INFO] Install Python ^(python.org^) or uv ^(docs.astral.sh/uv^) first.
    echo [INFO] If already installed, run from terminal or add to system PATH.
    pause
    exit /b 1
)

echo [INFO] Using Python: %PYTHON_EXE%

rem -- If .venv was created by uv (no pip), recreate with standard python
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m pip --version >nul 2>nul
    if errorlevel 1 (
        echo [INFO] .venv was created by uv (no pip), recreating...
        rmdir /s /q ".venv"
    ) else (
        set "PYTHON_EXE=.venv\Scripts\python.exe"
    )
)

if not exist ".venv" (
    echo [INFO] Creating virtual environment...
    "%PYTHON_EXE%" -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
    set "PYTHON_EXE=.venv\Scripts\python.exe"
)

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python executable not found: %PYTHON_EXE%
    pause
    exit /b 1
)

echo [INFO] Installing dependencies...
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] pip upgrade failed.
    pause
    exit /b 1
)

if exist "requirements.txt" (
    "%PYTHON_EXE%" -m pip install -r requirements.txt
) else (
    "%PYTHON_EXE%" -m pip install fastapi uvicorn nodriver playwright requests
)
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

echo [INFO] Starting server...
"%PYTHON_EXE%" main.py
echo.
echo [INFO] Server stopped with exit code %errorlevel%
pause
