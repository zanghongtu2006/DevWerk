from __future__ import annotations

from typing import Any


class FakeSettings:
    def __init__(self, db_path, session_dir):
        self.devwerk_db_path = str(db_path)
        self.devwerk_usage_tracking = False
        self.devwerk_session_dir = str(session_dir)


def configure_kanban(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store

    fake = FakeSettings(tmp_path / "devwerk.db", tmp_path / "sessions")
    monkeypatch.setattr(kanban_service, "settings", lambda: fake)
    monkeypatch.setattr(session_store, "settings", lambda: fake)
    kanban_service._initialized = False
    return kanban_service


def coding_workflow(name: str = "coding-workflow") -> dict[str, Any]:
    return {
        "name": name,
        "version": 1,
        "workflow_type": "coding",
        "requires_apply": True,
        "columns": [
            {
                "status_key": "analysis",
                "title": "Analysis",
                "position": 10,
                "transition_to": ["implementation", "failed"],
                "job_template": "analyze_request",
                "input_artifacts": ["workflow_request"],
                "output_artifact": "analysis_bundle",
                "success_action": "analysis_ready",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "implementation",
                "title": "Implementation",
                "position": 20,
                "transition_to": ["ready_to_apply", "failed"],
                "job_template": "implement_code_change",
                "input_artifacts": ["analysis_bundle"],
                "output_artifact": "code_change_bundle",
                "success_action": "code_ready",
                "failure_actions": ["fail"],
                "context_policy": {"output_contract": "code_change"},
            },
            {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 30, "transition_to": ["applied", "failed"]},
            {"status_key": "applied", "title": "Applied", "position": 40, "transition_to": ["verified", "failed"]},
            {"status_key": "verified", "title": "Verified", "position": 50, "transition_to": ["done", "failed"]},
            {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
            {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["analysis"]},
        ],
        "actions": {
            "analysis_ready": {"to": "analysis"},
            "code_ready": {"to": "ready_to_apply"},
            "apply_succeeded": {"to": "applied"},
            "verification_passed": {"to": "verified"},
            "verification_failed": {"to": "failed"},
            "workflow_done": {"to": "done"},
            "fail": {"to": "failed"},
            "abandon": {"to": "failed"},
            "retry": {"to": "analysis"},
        },
    }


def noncoding_workflow(name: str = "project-workflow", domain: str = "work") -> dict[str, Any]:
    return {
        "name": name,
        "version": 1,
        "columns": [
            {"status_key": "intake", "title": "Intake", "position": 10, "transition_to": ["prepared", "failed"]},
            {
                "status_key": "prepared",
                "title": f"{domain.title()} Prepared",
                "position": 20,
                "transition_to": ["reviewed", "failed"],
                "job_template": f"prepare_{domain}",
                "input_artifacts": ["workflow_request"],
                "output_artifact": f"{domain}_bundle",
                "success_action": "prepared",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "reviewed",
                "title": "Reviewed",
                "position": 30,
                "transition_to": ["done", "prepared", "failed"],
                "job_template": f"review_{domain}",
                "input_artifacts": [f"{domain}_bundle"],
                "output_artifact": "review_bundle",
                "success_action": "workflow_done",
                "failure_actions": ["request_rework", "fail"],
            },
            {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
            {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["intake"]},
        ],
        "actions": {
            "prepared": {"to": "prepared"},
            "request_rework": {"to": "prepared"},
            "workflow_done": {"to": "done"},
            "fail": {"to": "failed"},
            "abandon": {"to": "failed"},
            "retry": {"to": "intake"},
        },
    }
