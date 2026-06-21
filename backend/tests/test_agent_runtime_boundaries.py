from app.services.agent_definition import agent_catalog_from_dict
from app.services.capability_broker import CapabilityBroker
from app.services.job_scheduler import JobScheduler
from app.services.workflow_definition import workflow_from_dict


def test_column_job_and_agent_are_independently_configurable():
    workflow = workflow_from_dict(
        {
            "name": "custom",
            "columns": [
                {
                    "status_key": "analysis",
                    "title": "Analysis",
                    "position": 10,
                    "transition_to": ["done"],
                    "job_template": "analyze_change",
                },
                {"status_key": "done", "title": "Done", "position": 20, "transition_to": []},
            ],
            "actions": {"analysis_complete": {"to": "done"}},
        }
    )
    catalog = agent_catalog_from_dict(
        {
            "agents": [
                {
                    "id": "local-analysis-agent",
                    "roles": ["analysis"],
                    "runtime": "tool_loop",
                    "model_route": "local",
                    "capabilities": ["workspace.read"],
                },
                {
                    "id": "remote-analysis-agent",
                    "roles": ["analysis"],
                    "runtime": "tool_loop",
                    "model_route": "remote",
                    "capabilities": ["workspace.read", "workspace.search"],
                },
            ],
            "job_templates": [
                {
                    "id": "analyze_change",
                    "role": "analysis",
                    "output_contract": "analysis_bundle",
                    "required_capabilities": ["workspace.search"],
                }
            ],
        }
    )

    column = workflow.column("analysis")
    assert column is not None and column.job_template == "analyze_change"
    scheduled = JobScheduler(catalog).schedule(
        task_id="task-1",
        column=column.status_key,
        job_template=column.job_template,
    )
    assert scheduled.agent.id == "remote-analysis-agent"
    assert scheduled.template.output_contract == "analysis_bundle"


def test_capability_broker_maps_semantic_name_to_provider_implementation():
    declaration = {
        "provider": "ci-runner",
        "capabilities": [
            {
                "capability": "project.compile",
                "implementation": "pipeline.execute_compile_job",
            }
        ],
    }

    offer = CapabilityBroker().resolve(declaration, "project.compile")

    assert offer is not None
    assert offer.provider == "ci-runner"
    assert offer.implementation == "pipeline.execute_compile_job"
