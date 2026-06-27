$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "package-devwerk.ps1")
& (Join-Path $PSScriptRoot "package-idea-plugin.ps1")
Write-Host "All packages are under dist/"
