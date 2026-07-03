param(
    [string]$Version = "0.1.0",
    [switch]$SkipInstallers,
    [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "package-devwerk.ps1")
& (Join-Path $PSScriptRoot "package-idea-plugin.ps1")

if (-not $SkipInstallers) {
    & (Join-Path $PSScriptRoot "package-installers.ps1") -Version $Version
}

if (-not $SkipDocker) {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        & (Join-Path $PSScriptRoot "build-docker.ps1") -Image "devwerk:$Version"
    } else {
        Write-Warning "Skipping Docker image: docker was not found."
    }
}

Write-Host "All packages are under dist/"
