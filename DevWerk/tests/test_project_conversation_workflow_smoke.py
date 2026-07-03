from __future__ import annotations

import pytest

from tests.workflow_test_utils import coding_workflow, configure_kanban, noncoding_workflow


PROJECT_WORKFLOW_DESIGN_CASES = [
    {
        "id": "NL-CODE-ZH-001",
        "language": "zh",
        "domain": "coding",
        "message": "我准备做一个线下活动小程序，前端用 uniapp，后端用 Java。你帮我把这个项目的工作流搭起来。",
        "workflow": coding_workflow("mini-program-coding"),
    },
    {
        "id": "NL-CODE-EN-001",
        "language": "en",
        "domain": "coding",
        "message": "I want to build a SaaS backend for team accounts, login, billing states, and audit logs. Set up the project workflow first.",
        "workflow": coding_workflow("saas-backend-coding"),
    },
    {
        "id": "NL-CODE-DE-001",
        "language": "de",
        "domain": "iot",
        "message": "Ich möchte ein IoT-Dashboard entwickeln. Bitte richte zuerst einen Entwicklungsablauf ein.",
        "workflow": coding_workflow("iot-dashboard-coding"),
    },
    {
        "id": "NL-CODE-ES-001",
        "language": "es",
        "domain": "migration",
        "message": "Quiero migrar una API antigua. Primero necesito un flujo de trabajo para entender, diseñar, implementar y verificar.",
        "workflow": coding_workflow("api-migration-coding"),
    },
    {
        "id": "NL-CODE-JA-001",
        "language": "ja",
        "domain": "security",
        "message": "既存の管理画面で権限チェックに問題があります。まず調査、設計、修正、レビュー、検証のワークフローを作ってください。",
        "workflow": coding_workflow("permission-fix-coding"),
    },
    {
        "id": "NL-WRITE-ZH-001",
        "language": "zh",
        "domain": "writing",
        "message": "我想做一个公众号写作项目，流程包括选题、资料收集、写初稿、审核 AI 味、修改、最终发布。",
        "workflow": noncoding_workflow("writing-workflow", "writing"),
    },
    {
        "id": "NL-RESEARCH-EN-001",
        "language": "en",
        "domain": "research",
        "message": "I need a workflow for competitive research on AI developer tools: collect sources, compare products, verify claims, draft findings, and review conclusions.",
        "workflow": noncoding_workflow("research-workflow", "research"),
    },
    {
        "id": "NL-REVIEW-FR-001",
        "language": "fr",
        "domain": "review",
        "message": "Je veux mettre en place un processus de contrôle qualité pour des documents techniques.",
        "workflow": noncoding_workflow("review-workflow", "review"),
    },
    {
        "id": "NL-OPS-ES-001",
        "language": "es",
        "domain": "support",
        "message": "Necesito crear un proceso para gestionar casos de soporte: recibir, clasificar, investigar, responder y cerrar.",
        "workflow": noncoding_workflow("support-workflow", "support"),
    },
    {
        "id": "NL-EDU-EN-001",
        "language": "en",
        "domain": "education",
        "message": "I need a workflow for translating and improving a teacher operation manual.",
        "workflow": noncoding_workflow("education-workflow", "education"),
    },
    {
        "id": "NL-COMPLIANCE-ZH-001",
        "language": "zh",
        "domain": "compliance",
        "message": "我需要做一个合规审查项目，希望流程覆盖资料收集、条款拆解、风险识别、复核和报告。",
        "workflow": noncoding_workflow("compliance-workflow", "compliance"),
    },
    {
        "id": "NL-CODE-HI-001",
        "language": "hi",
        "domain": "inventory",
        "message": "मुझे एक छोटा inventory management system बनाना है। पहले project का workflow बनाइए।",
        "workflow": coding_workflow("inventory-coding"),
    },
]


@pytest.mark.parametrize("case", PROJECT_WORKFLOW_DESIGN_CASES, ids=lambda item: item["id"])
def test_project_conversation_workflow_design_smoke(monkeypatch, tmp_path, case):
    kanban_service = configure_kanban(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.services.workflow_designer as workflow_designer

    project_id = case["id"].lower()
    kanban_service.upsert_project(project_id=project_id, name=case["id"])

    monkeypatch.setattr(
        kanban_routes,
        "_ask_project_conversation_agent",
        lambda **kwargs: {
            "action": "save_design",
            "reply": "I will design the workflow.",
            "save": True,
        },
    )
    monkeypatch.setattr(
        workflow_designer,
        "_ask_llm",
        lambda **kwargs: {
            "reply": f"{case['id']} workflow ready.",
            "workflow": case["workflow"],
            "agents": {"project-default-agent": {"enabled": True, "model_route": "default"}},
        },
    )

    response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(action="message", message=case["message"]),
    )

    assert response["ok"] is True
    assert response["kind"] == "workflow_design"

    saved_workflow = kanban_service.get_project_workflow(project_id)["workflow"]
    known = {column["status_key"] for column in saved_workflow["columns"]}
    assert known
    for action in ("workflow_done", "fail", "abandon", "retry"):
        assert saved_workflow["actions"][action]["to"] in known
    if saved_workflow.get("workflow_type") == "coding" or saved_workflow.get("requires_apply"):
        assert {"ready_to_apply", "done", "failed"}.issubset(known)
        assert saved_workflow["actions"]["code_ready"]["to"] == "ready_to_apply"
        assert saved_workflow["actions"]["workflow_done"]["to"] == "done"

    events = kanban_service.list_events(project_id=project_id, limit=50)["events"]
    debug = [event for event in events if event["event_type"] == "project_workflow_design_debug"]
    assert debug
    assert debug[0]["payload"]["debug"]["llm_output"]["reply"].endswith("workflow ready.")
    assert kanban_service.list_columns(project_id)

