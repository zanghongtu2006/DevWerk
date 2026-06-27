param(
    [string]$Configuration = "release"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$App = Join-Path $Root "DevWerk"
$Dist = Join-Path $Root "dist"
$Stage = Join-Path $Dist "DevWerk"
$Package = Join-Path $Dist "devwerk-$Configuration.zip"

if (!(Test-Path $App)) {
    throw "DevWerk app directory not found: $App"
}

Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $Stage | Out-Null

$excludeExact = @(
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    "config/llm.json"
)
$excludeDirs = @(
    ".pytest_cache",
    "__pycache__",
    "data",
    "tests"
)

$AppRoot = (Resolve-Path -LiteralPath $App).Path.TrimEnd("\", "/")
Get-ChildItem -LiteralPath $App -Force -Recurse -File | ForEach-Object {
    $fullName = (Resolve-Path -LiteralPath $_.FullName).Path
    $relative = $fullName.Substring($AppRoot.Length).TrimStart("\", "/").Replace("\", "/")
    if ($excludeExact -contains $relative) { return }
    foreach ($dir in $excludeDirs) {
        if ($relative -eq $dir -or $relative.StartsWith("$dir/") -or $relative.Contains("/$dir/")) {
            return
        }
    }
    $destination = Join-Path $Stage $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
}

@'
@echo off
setlocal
cd /d "%~dp0"
py -3 -m venv .venv || python -m venv .venv
call .venv\Scripts\python.exe -m pip install --upgrade pip
call .venv\Scripts\pip.exe install -r requirements.txt
echo DevWerk installed. Copy config\llm.example.json to config\llm.json and set credentials before starting.
'@ | Set-Content -Path (Join-Path $Stage "install.bat") -Encoding ASCII

@'
@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe call install.bat
call .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
'@ | Set-Content -Path (Join-Path $Stage "start.bat") -Encoding ASCII

@'
#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo "DevWerk installed. Copy config/llm.example.json to config/llm.json and set credentials before starting."
'@ | Set-Content -Path (Join-Path $Stage "install.sh") -Encoding ASCII

@'
#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  sh ./install.sh
fi
. .venv/bin/activate
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
'@ | Set-Content -Path (Join-Path $Stage "start.sh") -Encoding ASCII

Remove-Item -LiteralPath $Package -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Package -Force
Write-Host "DevWerk package: $Package"
