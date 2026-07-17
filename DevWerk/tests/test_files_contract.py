from __future__ import annotations

import hashlib

import pytest

from app.v1.files import ProjectFiles


def test_project_files_reject_escape_and_write_hash_tracked_atomic_content(tmp_path):
    files = ProjectFiles(str(tmp_path / "project"))
    with pytest.raises(ValueError, match="escapes"):
        files.write_text("../escape.txt", "bad")

    first = files.write_text("nested/result.txt", "first")
    second = files.write_text("nested/result.txt", "second")
    assert files.read_text("nested/result.txt") == "second"
    assert second["sha256"] == hashlib.sha256(b"second").hexdigest()
    assert second["size"] == 6
    assert first["path"] == second["path"] == "nested/result.txt"
    assert list((tmp_path / "project" / "nested").glob("*.tmp")) == []


def test_context_file_reads_are_bounded_and_skip_build_directories(tmp_path):
    files = ProjectFiles(str(tmp_path / "project"))
    files.write_text("a.md", "a" * 10)
    files.write_text("nested/b.md", "b" * 10)
    files.write_text("node_modules/ignored.md", "secret")

    selected = files.existing_texts("**/*.md", max_total_chars=20)
    assert {item["path"] for item in selected} == {"a.md", "nested/b.md"}
    assert sum(len(item["content"]) for item in selected) <= 20
