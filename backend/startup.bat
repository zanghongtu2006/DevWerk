@echo off
setlocal EnableExtensions

rem DevWerk Backend - Windows startup script
rem Usage:
rem   startup.bat
rem   startup.bat development
rem   startup.bat production
rem   startup.bat test

cd /d "%~dp0"

rem Load .env as KEY=VALUE lines. Do not execute .env.
if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
        if not "%%~A"=="" (
            set "%%~A=%%~B"
        )
    )
)

rem Optional first argument overrides APP_ENV from .env.
if not "%~1"=="" (
    set "APP_ENV=%~1"
)

rem Defaults when .env is missing or incomplete.
if not defined APP_ENV set "APP_ENV=development"
if not defined HOST set "HOST=0.0.0.0"
if not defined PORT set "PORT=8000"
if not defined RELOAD set "RELOAD=false"
if not defined LOG_LEVEL set "LOG_LEVEL=debug"
if not defined UVICORN_ACCESS_LOG set "UVICORN_ACCESS_LOG=true"

if not "%APP_ENV%"=="development" if not "%APP_ENV%"=="production" if not "%APP_ENV%"=="test" (
    echo [DevWerk] Invalid APP_ENV: %APP_ENV%
    echo [DevWerk] Valid values: development ^| production ^| test
    exit /b 1
)

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

"%PYTHON_EXE%" --version >nul 2>&1
if errorlevel 1 (
    echo [DevWerk] Python not found. Install Python 3.10+ and retry.
    exit /b 1
)

if not exist "requirements.txt" (
    echo [DevWerk] requirements.txt not found. Are you in the backend directory?
    exit /b 1
)

set "UVICORN_RELOAD="
if /i "%RELOAD%"=="true" set "UVICORN_RELOAD=--reload"
if /i "%RELOAD%"=="1" set "UVICORN_RELOAD=--reload"
if /i "%RELOAD%"=="yes" set "UVICORN_RELOAD=--reload"

set "UVICORN_ACCESS_FLAG=--access-log"
if /i "%UVICORN_ACCESS_LOG%"=="false" set "UVICORN_ACCESS_FLAG=--no-access-log"
if /i "%UVICORN_ACCESS_LOG%"=="0" set "UVICORN_ACCESS_FLAG=--no-access-log"
if /i "%UVICORN_ACCESS_LOG%"=="no" set "UVICORN_ACCESS_FLAG=--no-access-log"

echo [DevWerk] Starting in %APP_ENV% mode...
echo [DevWerk] Starting uvicorn on http://%HOST%:%PORT% ...
echo [DevWerk] Log level:          %LOG_LEVEL%
echo [DevWerk] API docs:           http://localhost:%PORT%/docs
echo [DevWerk] Alternative docs:   http://localhost:%PORT%/redoc
echo.
echo [DevWerk] Press Ctrl+C to stop.
echo.

"%PYTHON_EXE%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [DevWerk] Missing backend dependencies for %PYTHON_EXE%.
    echo [DevWerk] Run: %PYTHON_EXE% -m pip install -r requirements.txt
    exit /b 1
)

"%PYTHON_EXE%" -m uvicorn app.main:app %UVICORN_RELOAD% --host %HOST% --port %PORT% --log-level %LOG_LEVEL% %UVICORN_ACCESS_FLAG%
