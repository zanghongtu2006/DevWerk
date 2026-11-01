"""Read-only evidence and user-equivalent conversation calls over HTTP."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
from pydantic import TypeAdapter

from evaluation.adapters.wire import (AgentRunDetailView, AgentRunView, BoardView,
                                      HealthView, JobView, ProjectView, QuiescenceView,
                                      SubmissionView, TaskView, VisibleMessageView)


class DevWerkHTTP:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", *, transport: httpx.BaseTransport | None = None):
        if base_url.rstrip("/") != "http://127.0.0.1:8000":
            raise ValueError("evaluation only connects to DevWerk on 127.0.0.1:8000")
        self.client = httpx.Client(base_url=base_url, transport=transport, timeout=30, trust_env=False)

    def __enter__(self) -> "DevWerkHTTP":
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    def get(self, path: str, **params: Any) -> Any:
        response = self.client.get("/v1" + path, params=params or None)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        response = self.client.post("/v1" + path, json=payload)
        response.raise_for_status()
        return response.json()

    def health(self) -> dict[str, Any]:
        return HealthView.model_validate(self.get("/health")).wire()

    def create_project(self, name: str, description: str, base_dir: str) -> dict[str, Any]:
        return ProjectView.model_validate(self.post("/projects", {
            "name": name, "description": description, "base_dir": base_dir})).wire()

    def submit(self, project_id: str, message: str) -> dict[str, Any]:
        return SubmissionView.model_validate(self.post(
            f"/projects/{project_id}/conversation", {"message": message})).wire()

    def job(self, project_id: str, job_id: str) -> dict[str, Any]:
        return JobView.model_validate(self.get(
            f"/projects/{project_id}/conversation-jobs/{job_id}")).wire()

    def project(self, project_id: str) -> dict[str, Any]:
        return ProjectView.model_validate(self.get(f"/projects/{project_id}")).wire()

    def board(self, project_id: str) -> dict[str, Any]:
        return BoardView.model_validate(self.get(f"/projects/{project_id}/board")).wire()

    def quiescence(self, project_id: str) -> dict[str, Any]:
        return QuiescenceView.model_validate(self.get(
            f"/projects/{project_id}/quiescence")).wire()

    def tasks(self, project_id: str) -> list[dict[str, Any]]:
        # The cursor is opaque; never infer it from a task ID.
        result: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            page = self.get(f"/projects/{project_id}/tasks", limit=100, **({"cursor": cursor} if cursor else {}))
            page = [item.wire() for item in TypeAdapter(list[TaskView]).validate_python(page)]
            result.extend(page)
            if len(page) < 100:
                return result
            candidate = page[-1]["id"]
            if candidate in seen:
                raise RuntimeError("Task pagination stopped advancing")
            seen.add(candidate)
            cursor = candidate

    def agent_runs(self, project_id: str) -> list[dict[str, Any]]:
        # The API has no cursor. A result at its configured cap is incomplete.
        runs = self.get(f"/projects/{project_id}/agent-runs", limit=500)
        runs = [item.wire() for item in TypeAdapter(list[AgentRunView]).validate_python(runs)]
        if len(runs) >= 500:
            raise RuntimeError("Agent Run list reached its API cap; evidence is incomplete")
        return runs

    def agent_run_pages(self, project_id: str, run_id: str) -> dict[str, Any]:
        path = f"/projects/{project_id}/agent-runs/{run_id}"
        first = self.get(path, after_sequence=0, limit=100)
        messages = list(self._pages(path, "messages", first))
        calls = list(self._pages(path, "tool_invocations", first))
        first["messages"] = messages
        first["tool_invocations"] = calls
        return AgentRunDetailView.model_validate(first).wire()

    def _pages(self, path: str, field: str, first: dict[str, Any]) -> Iterator[dict[str, Any]]:
        page = first[field]
        seen = {0}
        while True:
            yield from page
            if len(page) < 100:
                return
            cursor = page[-1]["sequence"]
            if cursor in seen:
                raise RuntimeError(f"{field} pagination stopped advancing")
            seen.add(cursor)
            page = self.get(path, after_sequence=cursor, limit=100)[field]

    def conversation(self, project_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        after = 0
        while True:
            page = self.get(f"/projects/{project_id}/conversation", after_id=after, limit=100)
            page = [item.wire() for item in TypeAdapter(list[VisibleMessageView]).validate_python(page)]
            result.extend(page)
            if len(page) < 100:
                return result
            latest = page[-1]["id"]
            if latest <= after:
                raise RuntimeError("Conversation pagination stopped advancing")
            after = latest

