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
    ".idea",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "data",
    "tests",
    "venv"
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

Remove-Item -LiteralPath $Package -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Package -Force
Write-Host "DevWerk package: $Package"
