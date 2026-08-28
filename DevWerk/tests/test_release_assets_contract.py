from __future__ import annotations

from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = APP_ROOT.parent


def test_release_launchers_are_checked_in_assets() -> None:
    for name in (
        "install.sh",
        "start.sh",
        "install.bat",
        "start.bat",
        "docker-start.sh",
        "Dockerfile",
        ".dockerignore",
    ):
        path = APP_ROOT / name
        assert path.is_file(), name
        assert path.read_text(encoding="utf-8").strip(), name

    assert not (REPOSITORY_ROOT / "packaging" / "Dockerfile").exists()
    assert not (REPOSITORY_ROOT / ".dockerignore").exists()

    attributes = (REPOSITORY_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "DevWerk/*.sh text eol=lf" in attributes
    assert "DevWerk/Dockerfile text eol=lf" in attributes


def test_release_packagers_copy_launchers_without_generating_them() -> None:
    shell_packager = (REPOSITORY_ROOT / "scripts" / "package-devwerk.sh").read_text(
        encoding="utf-8"
    )
    powershell_packager = (
        REPOSITORY_ROOT / "scripts" / "package-devwerk.ps1"
    ).read_text(encoding="utf-8")

    assert 'cat > "$STAGE/install.sh"' not in shell_packager
    assert 'cat > "$STAGE/start.sh"' not in shell_packager
    assert 'Join-Path $Stage "install.sh"' not in powershell_packager
    assert 'Join-Path $Stage "start.sh"' not in powershell_packager
    assert "--exclude='.venv'" in shell_packager
    assert "--exclude='venv'" in shell_packager
    assert 'PYTHON=python3' in shell_packager
    assert 'PYTHON=python' in shell_packager
    assert '".venv"' in powershell_packager
    assert '"venv"' in powershell_packager


def test_docker_builds_only_from_the_application_directory() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")
    shell_builder = (REPOSITORY_ROOT / "scripts" / "build-docker.sh").read_text(
        encoding="utf-8"
    )
    powershell_builder = (
        REPOSITORY_ROOT / "scripts" / "build-docker.ps1"
    ).read_text(encoding="utf-8")
    dockerfile = (APP_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "context: ./DevWerk" in workflow
    assert "file: ./DevWerk/Dockerfile" in workflow
    assert 'DOCKERFILE="$APP/Dockerfile"' in shell_builder
    assert 'docker build -f "$DOCKERFILE" -t "$IMAGE" "$APP"' in shell_builder
    assert '$Dockerfile = Join-Path $App "Dockerfile"' in powershell_builder
    assert "ENTRYPOINT [\"/opt/devwerk/docker-start.sh\"]" in dockerfile
    assert "COPY . ./" in dockerfile
