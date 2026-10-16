from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from app.v1.capabilities import CapabilityContext, _files_search
from app.v1.command_resolution import CommandNotFound
from app.v1.files import ProjectFiles
from app.v1.policy import ExecutionLimits
from app.v1.process_runner import run_command

def test_project_files_reject_escape_and_write_hash_tracked_atomic_content(tmp_path):
    files = ProjectFiles(str(tmp_path / "project"))
    with pytest.raises(ValueError, match="escapes"):
        files.write_text("../escape.txt", "bad")

    first = files.write_text("nested/result.txt", "first")
    second = files.write_text("nested/result.txt", "second")
    assert files.read_text("nested/result.txt") == "second"
    assert second["sha256"] == hashlib.sha256(b"second").hexdigest()
    assert second["size"] == 6
    assert second["utf8_characters"] == 6
    assert second["non_whitespace_characters"] == 6
    assert second["line_count"] == 1
    assert first["path"] == second["path"] == "nested/result.txt"
    assert list((tmp_path / "project" / "nested").glob("*.tmp")) == []


def test_project_file_read_pages_advance_by_unicode_characters(tmp_path):
    files = ProjectFiles(str(tmp_path / "project"))
    files.write_text("source.txt", "甲乙丙丁尾")

    first = files.read_text_page("source.txt", max_chars=2)
    assert first == {"path": "source.txt", "content": "甲乙", "offset": 0,
                     "next_offset": 2, "total_characters": 5, "content_complete": False}
    second = files.read_text_page("source.txt", offset=first["next_offset"], max_chars=2)
    assert second["content"] == "丙丁"
    assert second["next_offset"] == 4
    last = files.read_text_page("source.txt", offset=second["next_offset"], max_chars=2)
    assert last["content"] == "尾"
    assert last["next_offset"] is None
    assert last["content_complete"]
    assert files.read_text_page("source.txt")["content"] == files.read_text("source.txt")


def test_project_files_read_capability_exposes_next_offset(store, tmp_path):
    from app.v1.capabilities import CapabilityContext

    project = store.create_project("read-pages", "", str(tmp_path / "project"))
    files = ProjectFiles(project["base_dir"])
    files.write_text("source.txt", "甲乙丙丁")
    first = store.registry.dispatch("project.files.read",
        {"path": "source.txt", "max_chars": 2},
        CapabilityContext(project["id"], project, store))
    assert first.ok and first.output["content"] == "甲乙"
    second = store.registry.dispatch("project.files.read",
        {"path": "source.txt", "offset": first.output["next_offset"], "max_chars": 2},
        CapabilityContext(project["id"], project, store))
    assert second.ok and second.output["content"] == "丙丁"
    assert second.output["content_complete"]


def test_context_file_reads_respect_character_budget(tmp_path):
    files = ProjectFiles(str(tmp_path / "project"))
    files.write_text("a.md", "a" * 10)
    files.write_text("nested/b.md", "b" * 10)
    files.write_text("node_modules/ignored.md", "secret")

    selected = files.existing_texts("**/*.md", max_total_chars=20)
    assert {item["path"] for item in selected} == {"a.md", "nested/b.md"}
    assert sum(len(item["content"]) for item in selected) <= 20


def test_context_file_reads_skip_non_utf8_files_and_log_reason(tmp_path, caplog):
    files = ProjectFiles(str(tmp_path / "project"))
    files.write_text("readable.md", "usable context")
    binary = tmp_path / "project" / "image.bin"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")

    caplog.set_level("DEBUG", logger="devwerk.files")
    selected = files.existing_texts("**/*", max_total_chars=65_535)

    assert len(selected) == 1
    assert selected[0]["path"] == "readable.md"
    assert selected[0]["content"] == "usable context"
    assert selected[0]["utf8_characters"] == len("usable context")
    assert selected[0]["sha256"] == hashlib.sha256(b"usable context").hexdigest()
    assert "reason=non_utf8" in caplog.text
    assert "image.bin" in caplog.text


def test_project_files_verify_text_reports_exact_mismatch_and_match(tmp_path):
    files = ProjectFiles(str(tmp_path / "project"))
    files.write_text("proof.txt", "DEVWERK_CASE_A_OK")

    mismatch = files.verify_text(
        "proof.txt",
        {
            "expected_content": "DEVWERK_CASE_A_OK\n",
            "expected_ends_with_newline": True,
            "expected_line_count": 1,
        },
    )

    assert mismatch["outcome"] == "mismatch"
    assert not mismatch["matched"]
    assert mismatch["mismatches"] == [
        "expected_content",
        "expected_ends_with_newline",
    ]
    assert mismatch["actual"]["utf8_characters"] == 17
    assert not mismatch["actual"]["ends_with_newline"]

    files.write_text("proof.txt", "DEVWERK_CASE_A_OK\n")
    matched = files.verify_text(
        "proof.txt",
        {
            "expected_content": "DEVWERK_CASE_A_OK\n",
            "expected_ends_with_newline": True,
            "expected_size_bytes": 18,
            "expected_utf8_characters": 18,
        },
    )

    assert matched["outcome"] == "matched"
    assert matched["matched"]
    assert matched["mismatches"] == []


def test_project_files_verify_text_routes_missing_file_as_mismatch(tmp_path):
    files = ProjectFiles(str(tmp_path / "project"))

    result = files.verify_text(
        "not-created-yet.txt",
        {"expected_content": "READY\n"},
    )

    assert result["outcome"] == "mismatch"
    assert result["matched"] is False
    assert result["checks"] == {"expected_content": False}
    assert result["mismatches"] == ["expected_content"]
    assert result["actual"] == {
        "path": "not-created-yet.txt",
        "exists": False,
    }


# ---------------------------------------------------------------------------
# Command resolution
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != 'win32', reason='Windows batch launcher')
@pytest.mark.parametrize('prefix',['./','.\\'])
def test_explicit_current_directory_shim_uses_project_cwd(tmp_path, prefix):
    project = tmp_path/'project with spaces'
    project.mkdir()
    (project/'local-shim.cmd').write_text('@echo off\r\necho shim-ok %1\r\nexit /b 7\r\n')
    result = run_command([prefix+'local-shim.cmd','a & b'],project,ExecutionLimits())
    assert result['exit_code'] == 7
    assert 'shim-ok "a & b"' in result['stdout']


def test_native_unicode_quotes_and_metacharacters_remain_literal(tmp_path):
    arguments = ['中文 路径','say "yes"','a & b','%literal%!','尾部\\']
    result = run_command([sys.executable,'-X','utf8','-c',
        'import json,sys; print(json.dumps(sys.argv[1:],ensure_ascii=False))',*arguments],tmp_path,ExecutionLimits())
    assert result['exit_code'] == 0
    assert json.loads(result['stdout']) == arguments


def test_missing_explicit_command_rejects_before_process_admission(tmp_path):
    from contextlib import contextmanager
    admitted = []
    @contextmanager
    def guard():
        admitted.append(True)
        yield
    with pytest.raises(CommandNotFound):
        run_command(['./does-not-exist-devwerk-eval.cmd'],tmp_path,ExecutionLimits(),guard=guard)
    assert admitted == []


def test_missing_system_command_does_not_leave_uncertain_execution(store, tmp_path):
    from app.v1.capabilities import CapabilityContext

    project = store.create_project('missing system command', '', str(tmp_path / 'project'))
    result = store.registry.dispatch(
        'system.command.run',
        {'argv': ['./does-not-exist-devwerk-eval.cmd'], 'cwd': project['base_dir']},
        CapabilityContext(project['id'], project, store, agent_run_id='arun_test'),
    )

    assert not result.ok
    assert result.error['type'] == 'CommandNotFound'
    assert result.checkpoint['failure_disposition'] == 'rejected_before_effect'
    with store.connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM v1_execution_receipts WHERE project_id=? AND capability='system.command.run'",
            (project['id'],),
        ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Project search bounds
# ---------------------------------------------------------------------------

class _Checks:
    def __init__(self):
        self.calls = 0

    def check(self):
        self.calls += 1


def test_default_project_search_keeps_project_directories_and_bounds_large_files(store, tmp_path):
    project = store.create_project('bounded search', '', str(tmp_path / 'project'))
    root = Path(project['base_dir'])
    for relative, content in {
        'src/LoginTest.java': 'class LoginTest { // target phrase\n}\n',
        'frontend/node_modules/pkg/test.js': '// target phrase\n',
        'frontend/.npm-cache/cache.txt': 'target phrase\n',
        'backend/target/generated.java': '// target phrase\n',
        '.devwerk/internal.txt': 'target phrase\n',
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    (root / 'large.txt').write_text('target phrase\n' + 'x' * 1_000_001, encoding='utf-8')

    files = ProjectFiles(project['base_dir'], store.policy)
    paths = files.search_paths('**/*', limit=1000)
    assert 'src/LoginTest.java' in paths
    assert {'frontend/node_modules/pkg/test.js', 'frontend/.npm-cache/cache.txt',
            'backend/target/generated.java'} <= set(paths)
    assert '.devwerk/internal.txt' not in paths
    assert len(files.search_paths('**/*', limit=1)) == 1
    assert 'frontend/node_modules/pkg/test.js' in files.search_paths(
        'frontend/node_modules/**/*.js', limit=1000)

    checks = _Checks()
    ctx = CapabilityContext(project['id'], project, store, execution_control=checks)
    result = _files_search({'pattern': 'target phrase'}, ctx)
    assert {match['path'] for match in result['matches']} == {
        'src/LoginTest.java', 'frontend/node_modules/pkg/test.js',
        'frontend/.npm-cache/cache.txt', 'backend/target/generated.java'}
    assert checks.calls == len(paths)
