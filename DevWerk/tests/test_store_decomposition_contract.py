from __future__ import annotations

import inspect

from app.v1.repositories.artifact_repository import ArtifactRepository
from app.v1.repositories.event_repository import EventRepository
from app.v1.repositories.project_repository import ProjectRepository
from app.v1.repositories.schema_repository import SchemaRepository
from app.v1.services.recovery_manager import RecoveryManager
from app.v1.services.scheduler import SchedulerService
from app.v1.store import V1Store


def test_store_facade_composes_explicit_repositories_and_services(store):
    assert isinstance(store.projects, ProjectRepository)
    assert isinstance(store.artifact_repository, ArtifactRepository)
    assert isinstance(store.event_repository, EventRepository)
    assert isinstance(store.schema_repository, SchemaRepository)
    assert isinstance(store.scheduler, SchedulerService)
    assert isinstance(store.recovery_manager, RecoveryManager)


def test_store_facade_delegates_scheduler_without_duplicate_policy(monkeypatch, store):
    monkeypatch.setattr(store.scheduler, "runnable_task_ids", lambda limit=None: [f"limit:{limit}"])

    assert store.runnable_task_ids(7) == ["limit:7"]
    assert "self.scheduler.runnable_task_ids" in inspect.getsource(V1Store.runnable_task_ids)


def test_extracted_modules_do_not_import_the_store_facade():
    modules = (
        ProjectRepository,
        ArtifactRepository,
        EventRepository,
        SchemaRepository,
        SchedulerService,
        RecoveryManager,
    )

    for module_type in modules:
        source = inspect.getsource(inspect.getmodule(module_type))
        assert "from app.v1.store import" not in source
