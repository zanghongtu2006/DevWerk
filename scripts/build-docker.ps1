param(
    [string]$Image = "devwerk:local"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$App = Join-Path $Root "DevWerk"
$Dockerfile = Join-Path $App "Dockerfile"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker is required to build the DevWerk image."
}

docker build -f $Dockerfile -t $Image $App
Write-Host "DevWerk Docker image: $Image"
