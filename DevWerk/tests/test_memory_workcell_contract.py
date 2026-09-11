from __future__ import annotations

from app.v1.capabilities import build_core_registry
from app.v1.memory import FileMemoryStore
from app.v1.store import V1Store


def test_file_memory_is_project_local_searchable_and_supersedable(store, tmp_path):
    project = store.create_project("memory", "file-first memory", str(tmp_path / "project"))

    root = tmp_path / "project" / ".devwerk" / "memory"
    assert (root / "PROJECT.md").is_file()
    assert (root / "DECISIONS.md").is_file()

    first = store.memory_write(project["id"], {
        "kind": "decision",
        "scope": "project",
        "authority": "user_confirmed",
        "content": "Use PostgreSQL for the production deployment.",
        "source_type": "conversation",
        "source_id": "message-1",
    })
    assert first["reference"].endswith(".md")
    assert store.memory_search(project["id"], "PostgreSQL")[0]["metadata"]["id"] == first["metadata"]["id"]

    replacement = store.memory_supersede(project["id"], first["reference"], {
        "kind": "decision",
        "scope": "project",
        "authority": "user_confirmed",
        "content": "Use SQLite for the local-first v0.1.0 release.",
        "source_type": "conversation",
        "source_id": "message-2",
    })
    assert replacement["metadata"]["revision"] == 2
    assert store.memory_read(project["id"], first["reference"])["metadata"]["status"] == "superseded"
    appended = store.memory_append(
        project["id"],
        "CURRENT.md",
        "Implementation is in progress.",
        source_type="conversation",
        source_id="message-3",
    )
    assert appended["metadata"]["revision"] == 2
    assert "Implementation is in progress." in appended["content"]


def test_memory_store_provider_is_replaceable_without_runtime_branching(tmp_path):
    class TrackingFileMemoryStore(FileMemoryStore):
        name = "tracking-file"

        def __init__(self):
            self.writes = 0

        def write(self, project, record):
            self.writes += 1
            return super().write(project, record)

    provider = TrackingFileMemoryStore()
    value = V1Store(
        str(tmp_path / "provider.db"),
        registry=build_core_registry(),
        memory_store=provider,
    )
    project = value.create_project("provider", "replaceable", str(tmp_path / "project"))
    written = value.memory_write(project["id"], {
        "kind": "fact",
        "scope": "project",
        "content": "Provider substitution works.",
    })

    assert provider.writes == 1
    assert written["provider"] == "tracking-file"
    assert value.memory.build_context(project)["manifest"]["store_provider"] == "tracking-file"


def test_memory_context_initializes_a_pre_memory_project_workspace(store, tmp_path):
    project = store.projects.create_project(
        "existing",
        "created before File Memory",
        str(tmp_path / "existing"),
        "",
    )
    memory_root = tmp_path / "existing" / ".devwerk" / "memory"
    assert not memory_root.exists()

    context = store.memory.build_context(project)

    assert (memory_root / "PROJECT.md").is_file()
    assert context["manifest"]["selected"]
