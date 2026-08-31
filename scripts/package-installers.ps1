param(
    [string]$Version = "0.1.0",
    [string]$Arch = "amd64",
    [switch]$StrictTools
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Dist = Join-Path $Root "dist"
$Stage = Join-Path $Dist "DevWerk"
$Installers = Join-Path $Dist "installers"
$Packaging = Join-Path $Dist "packaging"

New-Item -ItemType Directory -Path $Installers, $Packaging -Force | Out-Null
& (Join-Path $PSScriptRoot "package-devwerk.ps1") -Configuration "release"

$Tar = Get-Command tar -ErrorAction SilentlyContinue
if ($Tar) {
    & $Tar.Source -C $Dist -czf (Join-Path $Installers "devwerk-$Version-universal.tar.gz") "DevWerk"
} else {
    Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath (Join-Path $Installers "devwerk-$Version-universal.zip") -Force
}

$Service = @'
[Unit]
Description=DevWerk backend
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/devwerk
ExecStart=/opt/devwerk/startup.sh production
ExecStop=/opt/devwerk/shutdown.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
'@
$ServicePath = Join-Path $Packaging "devwerk.service"
$Service | Set-Content -Path $ServicePath -Encoding ASCII

$PostInstall = @'
#!/usr/bin/env sh
set -eu
chmod +x /opt/devwerk/install.sh /opt/devwerk/startup.sh /opt/devwerk/shutdown.sh || true
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
fi
'@
$PreRemove = @'
#!/usr/bin/env sh
set -eu
if command -v systemctl >/dev/null 2>&1; then
  systemctl stop devwerk || true
  systemctl disable devwerk || true
fi
'@
$PostInstallPath = Join-Path $Packaging "postinstall.sh"
$PreRemovePath = Join-Path $Packaging "preremove.sh"
$PostInstall | Set-Content -Path $PostInstallPath -Encoding ASCII
$PreRemove | Set-Content -Path $PreRemovePath -Encoding ASCII

$Nfpm = @"
name: devwerk
arch: $Arch
platform: linux
version: $Version
section: default
priority: optional
maintainer: DevWerk <devwerk@example.local>
description: DevWerk backend workflow and Kanban agent runtime.
license: Apache-2.0
contents:
  - src: $($Stage.Replace("\", "/"))/
    dst: /opt/devwerk
  - src: $($ServicePath.Replace("\", "/"))
    dst: /etc/systemd/system/devwerk.service
scripts:
  postinstall: $($PostInstallPath.Replace("\", "/"))
  preremove: $($PreRemovePath.Replace("\", "/"))
"@
$NfpmPath = Join-Path $Packaging "nfpm.yaml"
$Nfpm | Set-Content -Path $NfpmPath -Encoding ASCII

function Invoke-NfpmPackage {
    param([string]$Packager)
    $NfpmCmd = Get-Command nfpm -ErrorAction SilentlyContinue
    if ($NfpmCmd) {
        & $NfpmCmd.Source package --packager $Packager --config $NfpmPath --target $Installers
        return
    }
    $DockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($DockerCmd) {
        & $DockerCmd.Source run --rm -v "$($Root):/work" -w /work goreleaser/nfpm:v2 package --packager $Packager --config /work/dist/packaging/nfpm.yaml --target /work/dist/installers
        return
    }
    if ($StrictTools) {
        throw "nfpm or docker is required to build $Packager packages."
    }
    Write-Warning "Skipping $Packager package: install nfpm or docker."
}

Invoke-NfpmPackage -Packager "deb"
Invoke-NfpmPackage -Packager "rpm"

if ($IsMacOS -and (Get-Command pkgbuild -ErrorAction SilentlyContinue) -and (Get-Command productbuild -ErrorAction SilentlyContinue)) {
    $PkgRoot = Join-Path $Packaging "macos-root"
    Remove-Item -LiteralPath $PkgRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path (Join-Path $PkgRoot "Applications/DevWerk") -Force | Out-Null
    Copy-Item -Path (Join-Path $Stage "*") -Destination (Join-Path $PkgRoot "Applications/DevWerk") -Recurse -Force
    pkgbuild --root $PkgRoot --identifier dev.devwerk.backend --version $Version (Join-Path $Packaging "devwerk-component.pkg")
    productbuild --package (Join-Path $Packaging "devwerk-component.pkg") (Join-Path $Installers "devwerk-$Version-macos.pkg")
} else {
    @'
#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
DEVWERK_VERSION="${DEVWERK_VERSION:-0.1.0}" "$ROOT/scripts/package-installers.sh"
'@ | Set-Content -Path (Join-Path $Installers "build-macos-pkg.sh") -Encoding ASCII
    Write-Warning "macOS pkg requires macOS pkgbuild/productbuild; helper written to dist/installers/build-macos-pkg.sh"
}

$Makensis = Get-Command makensis -ErrorAction SilentlyContinue
if ($Makensis) {
    & $Makensis.Source "/DVERSION=$Version" "/DROOT=$Root" "/DOUT=$(Join-Path $Installers "devwerk-$Version-windows.exe")" (Join-Path $Root "packaging/windows-devwerk.nsi")
} else {
    Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath (Join-Path $Installers "devwerk-$Version-windows-portable.zip") -Force
    Copy-Item -LiteralPath (Join-Path $Root "packaging/windows-devwerk.nsi") -Destination (Join-Path $Installers "windows-devwerk.nsi") -Force
    Write-Warning "Windows exe requires NSIS makensis; portable zip and NSIS script were generated."
}

Write-Host "Installers are under $Installers"
