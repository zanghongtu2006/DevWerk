$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Plugin = Join-Path $Root "idea-plugin"
$Dist = Join-Path $Root "dist\idea-plugin"

if (!(Test-Path $Plugin)) {
    throw "Plugin directory not found: $Plugin"
}

Push-Location $Plugin
try {
    .\gradlew.bat buildPlugin
}
finally {
    Pop-Location
}

New-Item -ItemType Directory -Path $Dist -Force | Out-Null
Get-ChildItem -Path (Join-Path $Plugin "build\distributions") -Filter "*.zip" | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Dist $_.Name) -Force
    Write-Host "IntelliJ-family plugin package: $(Join-Path $Dist $_.Name)"
}
