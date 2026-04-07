@echo off
:: DevWerk Backend — Quick Startup Script (Windows batch)
::
:: Usage:
::   startup.bat              — runs with current .env settings
::   startup.bat development  — forces APP_ENV=development
::   startup.bat production   — forces APP_ENV=production
::
:: Prerequisites:
::   1. pip install -r requirements.txt
::   2. cp .env.example .env    (and fill in your values)

setlocal

:: ── Detect environment ────────────────────────────────────────────────────
if "%1"=="" (
    for /f "usebackq tokens=1,* delims==" %%a in (`.env 2^>nul`) do (
        if "%%a"=="APP_ENV" set "APP_ENV=%%b"
    )
    if not defined APP_ENV set "APP_ENV=development"
) else (
    set "APP_ENV=%1"
)

:: ── Validate APP_ENV ──────────────────────────────────────────────────────
if not "%APP_ENV%"=="development" if not "%APP_ENV%"=="production" if not "%APP_ENV%"=="test" (
    echo [DevWerk] Invalid APP_ENV: %APP_ENV%
    echo Valid values: development ^|^ production ^|^ test
    exit /b 1
)

echo [DevWerk] Starting in %APP_ENV% mode...

:: ── Check Python ──────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [DevWerk] Python not found. Install Python 3.10+ and retry.
    exit /b 1
)

:: ── Check requirements ────────────────────────────────────────────────────
if not exist requirements.txt (
    echo [DevWerk] requirements.txt not found. Are you in the backend directory?
    exit /b 1
)

:: ── Load environment variables from .env ──────────────────────────────────
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (`.env 2^>nul`) do (
        set "%%a=%%b"
    )
)

:: ── Start uvicorn ─────────────────────────────────────────────────────────
echo [DevWerk] Starting uvicorn on http://%HOST%:%PORT% ...
echo [DevWerk] API docs:           http://localhost:%PORT%/docs
echo [DevWerk] Alternative docs:   http://localhost:%PORT%/redoc
echo.
echo [DevWerk] Press Ctrl+C to stop.
echo.

uvicorn app.main:app --reload --host %HOST% --port %PORT%
