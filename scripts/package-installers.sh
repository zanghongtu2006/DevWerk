#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
DIST="$ROOT/dist"
STAGE="$DIST/DevWerk"
INSTALLERS="$DIST/installers"
PACKAGING="$DIST/packaging"
VERSION="${DEVWERK_VERSION:-0.1.0}"
ARCH="${DEVWERK_ARCH:-$(uname -m)}"
SKIP_MISSING="${DEVWERK_SKIP_MISSING_TOOLS:-1}"

mkdir -p "$INSTALLERS" "$PACKAGING"
sh "$ROOT/scripts/package-devwerk.sh"

tar -C "$DIST" -czf "$INSTALLERS/devwerk-$VERSION-universal.tar.gz" DevWerk

cat > "$PACKAGING/devwerk.service" <<'EOF'
[Unit]
Description=DevWerk backend
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/devwerk
ExecStart=/opt/devwerk/start.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > "$PACKAGING/nfpm.yaml" <<EOF
name: devwerk
arch: $ARCH
platform: linux
version: $VERSION
section: default
priority: optional
maintainer: DevWerk <devwerk@example.local>
description: DevWerk backend workflow and Kanban agent runtime.
license: Apache-2.0
contents:
  - src: $STAGE/
    dst: /opt/devwerk
  - src: $PACKAGING/devwerk.service
    dst: /etc/systemd/system/devwerk.service
scripts:
  postinstall: $PACKAGING/postinstall.sh
  preremove: $PACKAGING/preremove.sh
EOF

cat > "$PACKAGING/postinstall.sh" <<'EOF'
#!/usr/bin/env sh
set -eu
chmod +x /opt/devwerk/install.sh /opt/devwerk/start.sh || true
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
fi
EOF

cat > "$PACKAGING/preremove.sh" <<'EOF'
#!/usr/bin/env sh
set -eu
if command -v systemctl >/dev/null 2>&1; then
  systemctl stop devwerk || true
  systemctl disable devwerk || true
fi
EOF
chmod +x "$PACKAGING/postinstall.sh" "$PACKAGING/preremove.sh"

run_nfpm() {
  packager="$1"
  if command -v nfpm >/dev/null 2>&1; then
    nfpm package --packager "$packager" --config "$PACKAGING/nfpm.yaml" --target "$INSTALLERS"
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    docker run --rm -v "$ROOT:/work" -w /work goreleaser/nfpm:v2 package --packager "$packager" --config "/work/dist/packaging/nfpm.yaml" --target "/work/dist/installers"
    return
  fi
  if [ "$SKIP_MISSING" = "1" ]; then
    echo "Skipping $packager package: install nfpm or docker." >&2
    return
  fi
  echo "nfpm or docker is required to build $packager packages." >&2
  exit 1
}

run_nfpm deb
run_nfpm rpm

if [ "$(uname -s)" = "Darwin" ] && command -v pkgbuild >/dev/null 2>&1 && command -v productbuild >/dev/null 2>&1; then
  PKGROOT="$PACKAGING/macos-root"
  rm -rf "$PKGROOT"
  mkdir -p "$PKGROOT/Applications/DevWerk"
  cp -R "$STAGE/." "$PKGROOT/Applications/DevWerk/"
  pkgbuild --root "$PKGROOT" --identifier dev.devwerk.backend --version "$VERSION" "$PACKAGING/devwerk-component.pkg"
  productbuild --package "$PACKAGING/devwerk-component.pkg" "$INSTALLERS/devwerk-$VERSION-macos.pkg"
else
  cat > "$INSTALLERS/build-macos-pkg.sh" <<'EOF'
#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
DEVWERK_VERSION="${DEVWERK_VERSION:-0.1.0}" "$ROOT/scripts/package-installers.sh"
EOF
  chmod +x "$INSTALLERS/build-macos-pkg.sh"
  echo "macOS pkg requires macOS pkgbuild/productbuild; helper written to dist/installers/build-macos-pkg.sh" >&2
fi

if command -v makensis >/dev/null 2>&1; then
  makensis /DVERSION="$VERSION" /DROOT="$ROOT" /DOUT="$INSTALLERS/devwerk-$VERSION-windows.exe" "$ROOT/packaging/windows-devwerk.nsi"
else
  if command -v zip >/dev/null 2>&1; then
    (cd "$STAGE" && zip -qr "$INSTALLERS/devwerk-$VERSION-windows-portable.zip" .)
  else
    python -c "import shutil; shutil.make_archive('$INSTALLERS/devwerk-$VERSION-windows-portable', 'zip', '$STAGE')"
  fi
  cp "$ROOT/packaging/windows-devwerk.nsi" "$INSTALLERS/windows-devwerk.nsi"
  echo "Windows exe requires NSIS makensis; portable zip and NSIS script were generated." >&2
fi

echo "Installers are under $INSTALLERS"
