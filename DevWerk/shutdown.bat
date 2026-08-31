@echo off
setlocal EnableExtensions

rem DevWerk Service - Windows shutdown script
cd /d "%~dp0"

set "PYTHON_EXE=%CD%\venv\Scripts\python.exe"
set "DEVWERK_RESTART_MARKER=%CD%\data\restart.request"

if exist "%DEVWERK_RESTART_MARKER%" del /q "%DEVWERK_RESTART_MARKER%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$python = [IO.Path]::GetFullPath('%PYTHON_EXE%');" ^
  "$matches = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath) -ieq $python) -and $_.CommandLine -like '*-m uvicorn app.main:app*' });" ^
  "if ($matches.Count -eq 0) { Write-Host '[DevWerk] No running DevWerk service found.'; exit 0 };" ^
  "$matches | ForEach-Object { Write-Host ('[DevWerk] Stopping process ' + $_.ProcessId + '...'); Stop-Process -Id $_.ProcessId -Force };"

if errorlevel 1 (
    echo [DevWerk] Failed to stop the DevWerk service.
    exit /b 1
)

echo [DevWerk] DevWerk service stopped.
