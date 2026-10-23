"""Class-owned context frame for the Project Conversation Agent."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.v1.conversations.conversation_intent import WorkIntentState
from app.v1.protocols.conversation.conversation_intent_contracts import RequirementSnapshot, UserTurnContract
from app.v1.protocols.context.memory_output_contracts import MemoryContextReceipt
from app.v1.protocols.tools.planning.capability_contracts import TaskPlanContractReceipt


class ActiveWorkflowContext(BaseModel):
    id: str
    revision: int
    name: str | None = None
    requirement_id: str | None = None
    requirement_revision: int | None = None


class CurrentTaskPlanContractContext(BaseModel):
    task_plan_contract: TaskPlanContractReceipt | None = None


class SavedPlanReference(BaseModel):
    id: str
    created_at: str
    content_hash: str | None = None


class MailboxEventReference(BaseModel):
    id: int
    event_type: str
    task_id: str | None
    run_id: str | None


class ConversationJobContext(BaseModel):
    id: str
    start_task: bool
    trigger_kind: str
    trigger: dict[str, JsonValue]
    task_graph_mutation_allowed: bool


class TurnAuthorityContext(BaseModel):
    task_graph_mutation_allowed: bool
    instruction: str


class CurrentRequestContext(BaseModel):
    message_id: int | None
    content: str
    trigger_kind: str
    trigger: dict[str, JsonValue]
    job_id: str


class ConversationContextFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_contract: UserTurnContract
    work_intent: WorkIntentState
    requested_mode: str
    user_action: JsonValue
    requirement: RequirementSnapshot | None
    workers: list[dict[str, JsonValue]]
    active_workflow: ActiveWorkflowContext | None
    global_settings: dict[str, JsonValue]
    memory: MemoryContextReceipt
    loops: list[dict[str, JsonValue]]
    workflow_plans: list[SavedPlanReference]
    task_plans: list[SavedPlanReference]
    tasks: list[dict[str, JsonValue]]
    mailbox: list[MailboxEventReference]
    conversation_job: ConversationJobContext
    turn_authority: TurnAuthorityContext
    current_request: CurrentRequestContext
