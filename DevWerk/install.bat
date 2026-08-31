@echo off
setlocal EnableExtensions

cd /d "%~dp0"

py -3 -m venv venv || python -m venv venv
call venv\Scripts\python.exe -m pip install --upgrade pip
call venv\Scripts\pip.exe install -r requirements.txt

echo DevWerk installed. Copy config\llm.example.json to config\llm.json and set credentials before starting.
