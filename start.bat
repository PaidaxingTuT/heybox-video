@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%backend"
set "VENV_DIR=%BACKEND_DIR%\.venv"
set "PYTHON_EXE="

if not exist "%BACKEND_DIR%" (
    echo [ERROR] Backend directory not found: "%BACKEND_DIR%"
    exit /b 1
)

where uv >nul 2>nul
if %errorlevel%==0 (
    echo [INFO] Detected uv, starting with uv...
    pushd "%BACKEND_DIR%"
    call uv sync
    if errorlevel 1 (
        echo [ERROR] uv sync failed.
        popd
        exit /b 1
    )
    call uv run start
    set "EXIT_CODE=%errorlevel%"
    popd
    exit /b %EXIT_CODE%
)

echo [INFO] uv not found, falling back to python/pip...

if exist "%VENV_DIR%\Scripts\python.exe" (
    set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        echo [INFO] Creating virtual environment with py...
        pushd "%BACKEND_DIR%"
        call py -3 -m venv .venv
        set "VENV_CODE=%errorlevel%"
        popd
        if not "%VENV_CODE%"=="0" (
            echo [ERROR] Failed to create virtual environment with py.
            exit /b 1
        )
        set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
    ) else (
        where python >nul 2>nul
        if not %errorlevel%==0 (
            echo [ERROR] Neither uv, py, nor python was found in PATH.
            exit /b 1
        )
        echo [INFO] Creating virtual environment with python...
        pushd "%BACKEND_DIR%"
        call python -m venv .venv
        set "VENV_CODE=%errorlevel%"
        popd
        if not "%VENV_CODE%"=="0" (
            echo [ERROR] Failed to create virtual environment with python.
            exit /b 1
        )
        set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
    )
)

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python executable not found: "%PYTHON_EXE%"
    exit /b 1
)

echo [INFO] Installing backend dependencies...
pushd "%BACKEND_DIR%"
call "%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip.
    popd
    exit /b 1
)

call "%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install requirements.
    popd
    exit /b 1
)

echo [INFO] Starting backend service...
call "%PYTHON_EXE%" main.py
set "EXIT_CODE=%errorlevel%"
popd

exit /b %EXIT_CODE%
