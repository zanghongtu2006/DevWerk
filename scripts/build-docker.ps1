param(
    [string]$Image = "devwerk:local"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Dockerfile = Join-Path $Root "packaging/Dockerfile"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker is required to build the DevWerk image."
}

docker build -f $Dockerfile -t $Image $Root
Write-Host "DevWerk Docker image: $Image"
