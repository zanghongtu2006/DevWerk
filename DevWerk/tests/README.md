# Test cases

The deterministic suite is organized by the behavior being verified. Put a new case beside the closest existing scenario; keep shared construction helpers in `helpers.py` and fixtures in `conftest.py`.

| Scenario | Modules | Collected cases |
| --- | --- | ---: |
| Conversation turns, scope and supervision | `test_conversation_contract.py`, `test_conversation_intent_contract.py`, `test_scope_extension.py`, `test_project_supervision.py` | 123 |
| Workflow definition, execution, recovery and completion | `test_domain_contract.py`, `test_loop_contract.py`, `test_runtime_contract.py`, `test_p0_runtime_regressions.py`, `test_completion_protocol.py`, `test_repair_notification_contract.py`, `test_task_entry_admission.py`, `test_orchestration_policy_contract.py` | 177 |
| Worker lifecycle, context and memory | `test_p0_assignment_regressions.py`, `test_context_lifecycle.py`, `test_context_acceptance_regression.py`, `test_memory_scope_budget.py` | 98 |
| Tools, provider and Web API | `test_capability_contract.py`, `test_files_contract.py`, `test_provider_contract.py`, `test_api_web_contract.py` | 86 |
| Settings, persistence, release and evaluation boundaries | `test_global_settings_contract.py`, `test_architecture_conformance.py`, `test_store_contract.py`, `test_store_decomposition_contract.py`, `test_software_context_eval.py` | 54 |

Total: **25 modules, 538 collected cases**. The dated [consolidation plan](../docs/archive/2026-10-16/2026-10-16-test-case-suite-consolidation-plan.md) maps the former modules to these scenario files; the [verification record](../docs/archive/2026-10-16/2026-10-16-test-suite-consolidation-verification.md) records collection and regression results.

Run the suite from `DevWerk/`:

```powershell
.\venv\Scripts\python.exe -m pytest tests -q
```

For a focused change, select the relevant scenario module first, then run the complete suite. Compare collection counts before and after moving tests; a shorter file list is not a substitute for preserving assertions and parameterized cases.
