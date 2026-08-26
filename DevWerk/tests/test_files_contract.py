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

    assert selected == [{"path": "readable.md", "content": "usable context"}]
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
