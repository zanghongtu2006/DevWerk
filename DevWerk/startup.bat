@echo off
setlocal EnableExtensions

rem DevWerk Service - Windows startup script
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

set "PYTHON_EXE=venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo [DevWerk] Project virtual environment not found: %CD%\%PYTHON_EXE%
    echo [DevWerk] Restore the existing DevWerk venv before starting the service.
    exit /b 1
)

"%PYTHON_EXE%" --version >nul 2>&1
if errorlevel 1 (
    echo [DevWerk] Project virtual environment cannot be executed: %CD%\%PYTHON_EXE%
    echo [DevWerk] DevWerk will not fall back to a system Python interpreter.
    exit /b 1
)

if not exist "requirements.txt" (
    echo [DevWerk] requirements.txt not found. Are you in the DevWerk service directory?
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

"%PYTHON_EXE%" -c "import fastapi, pydantic, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [DevWerk] Service dependencies are missing or out of date.
    echo [DevWerk] Installing requirements into %PYTHON_EXE% ...
    "%PYTHON_EXE%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo [DevWerk] Dependency installation failed.
        exit /b 1
    )
)

"%PYTHON_EXE%" -c "import fastapi, pydantic, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [DevWerk] Dependency verification failed after installation.
    exit /b 1
)

echo [DevWerk] Starting in %APP_ENV% mode...
echo [DevWerk] Python:             %CD%\%PYTHON_EXE%
echo [DevWerk] Starting uvicorn on http://%HOST%:%PORT% ...
echo [DevWerk] Log level:          %LOG_LEVEL%
echo [DevWerk] API docs:           http://localhost:%PORT%/docs
echo [DevWerk] Alternative docs:   http://localhost:%PORT%/redoc
echo [DevWerk] Web workbench:      http://localhost:%PORT%/
echo [DevWerk] Health endpoint:    http://localhost:%PORT%/v1/health
echo.
echo [DevWerk] Press Ctrl+C to stop.
echo.

"%PYTHON_EXE%" -m uvicorn app.main:app %UVICORN_RELOAD% --host %HOST% --port %PORT% --log-level %LOG_LEVEL% %UVICORN_ACCESS_FLAG%
