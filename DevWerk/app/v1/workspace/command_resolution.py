"""Resolve launch targets before effect admission, retaining argv for native tools."""
from __future__ import annotations

import os
from pathlib import Path
import shutil


class CommandNotFound(ValueError):
    error_code = 'command_not_found'


def resolve_launch(argv: list[str], cwd: Path, environment_profile: str) -> list[str]:
    resolved = resolve_command(argv, cwd)
    if environment_profile not in {'inherit', 'python_utf8'}:
        raise ValueError(f'Unknown command environment profile: {environment_profile}')
    if environment_profile == 'python_utf8' and not supports_python_utf8_profile(resolved):
        raise ValueError('python_utf8 requires a direct Python interpreter executable; use inherit for other commands')
    return resolved


def resolve_command(argv: list[str], cwd: Path) -> list[str]:
    if not argv or any('\x00' in arg for arg in argv):
        raise ValueError('Command argv must be nonempty and contain no NUL')
    executable = argv[0]
    candidate = Path(executable)
    if candidate.is_absolute() or candidate.parent != Path('.') or executable.startswith(('./', '.\\')):
        target = candidate if candidate.is_absolute() else cwd / candidate
        resolved = str(target.resolve()) if target.is_file() else None
    else:
        resolved = shutil.which(executable)
    if not resolved:
        raise CommandNotFound(f'Executable not found: {executable}; check PATH/PATHEXT or use an explicit launcher')
    if os.name != 'nt' or Path(resolved).suffix.lower() not in {'.cmd', '.bat'}:
        return [resolved, *argv[1:]]
    # cmd shims require shell syntax. Quote every argument; do not accept data
    # that could be expanded/reparsed differently by cmd. Callers needing shell
    # syntax can explicitly choose their launcher instead of silent rewriting.
    if any(any(ch in arg for ch in '"%!\r\n') for arg in [resolved, *argv[1:]]):
        raise ValueError('Batch command arguments contain expansion/quote characters; use an explicit launcher')
    shell = os.environ.get('COMSPEC') or shutil.which('cmd.exe')
    if not shell:
        raise CommandNotFound('cmd.exe is required to launch a Windows batch shim')
    command = ' '.join('"'+arg+'"' for arg in [resolved, *argv[1:]])
    return [shell, '/d', '/s', '/v:off', '/c', '"'+command+'"']


def supports_python_utf8_profile(resolved_argv: list[str]) -> bool:
    """Only direct interpreter execution may opt into Python's UTF-8 mode."""
    name = Path(resolved_argv[0]).name.casefold()
    stem = name.removesuffix('.exe')
    return name in {'python', 'python.exe', 'python3', 'python3.exe', 'pythonw.exe'} or (
        stem.startswith('python3.') and stem.removeprefix('python3.').isdigit())
