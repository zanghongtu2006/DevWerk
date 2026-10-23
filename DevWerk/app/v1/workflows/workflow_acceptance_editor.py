"""Typed edit request for checks on one frozen Workflow revision."""

from pydantic import BaseModel, ConfigDict, Field

from app.v1.domain import AcceptanceCheck, AcceptanceObligation, ColumnDefinition, EdgeCheck, Transition, WorkflowDefinition


class _EditInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransitionCheckEdit(_EditInput):
    outcome: str
    required_checks: list[EdgeCheck]


class ColumnCheckEdit(_EditInput):
    key: str
    acceptance_checks: list[AcceptanceCheck]
    transitions: list[TransitionCheckEdit] = Field(default_factory=list)


class WorkflowAcceptanceEdit(_EditInput):
    base_revision_id: str
    base_digest: str
    columns: list[ColumnCheckEdit] = Field(min_length=1)
    acceptance_obligations: list[AcceptanceObligation] = Field(default=None)

    def apply(self, workflow: WorkflowDefinition) -> tuple[WorkflowDefinition, list[str]]:
        columns = {column.key: column for column in workflow.columns}
        seen: set[str] = set()
        for edit in self.columns:
            if edit.key not in columns or edit.key in seen:
                raise ValueError("Unknown or duplicate column; legal columns: " + ", ".join(columns))
            seen.add(edit.key)
            original = columns[edit.key]
            edges: dict[str, Transition] = {edge.outcome: edge for edge in original.transitions}
            for change in edit.transitions:
                if change.outcome not in edges:
                    raise ValueError("Unknown outcome for Column " + edit.key + ": " + change.outcome)
                edges[change.outcome] = edges[change.outcome].model_copy(
                    update={"required_checks": change.required_checks}
                )
            columns[edit.key] = original.model_copy(update={
                "acceptance_checks": edit.acceptance_checks,
                "transitions": [edges[edge.outcome] for edge in original.transitions],
            })
        updated = workflow.model_copy(update={"columns": [columns[column.key] for column in workflow.columns]})
        if "acceptance_obligations" in self.model_fields_set:
            updated.acceptance_obligations = self.acceptance_obligations
        return WorkflowDefinition.model_validate(updated.model_dump(mode="json")), sorted(seen)
