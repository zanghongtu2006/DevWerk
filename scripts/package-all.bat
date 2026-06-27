@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0package-all.ps1"
