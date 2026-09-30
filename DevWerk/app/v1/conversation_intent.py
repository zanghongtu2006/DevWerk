"""Conversation-owned semantic interpretation; no keyword-based execution inference."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class UserEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message_id: int
    quote: str | None = Field(default=None, min_length=1, max_length=30000,
        description='Optional exact excerpt. Prefer message_id alone; never paraphrase a quote.')


class IntentDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=10000)
    status: Literal['user_decided', 'agent_proposed', 'delegated_choice']
    source_message_ids: list[int] = Field(default_factory=list)


class IntentQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=4000)
    state: Literal['open', 'resolved', 'delegated'] = 'open'
    blocking_for_start: bool = True
    source_message_ids: list[int] = Field(default_factory=list)


class IntentQuestionUpdate(IntentQuestion):
    question: str | None = Field(default=None, min_length=1, max_length=4000,
        description='Required for a new key. Omit when only changing the state of an existing question.')


class TurnResolution(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_message_id: int
    based_on_intent_revision: int = Field(ge=0)
    act: Literal['discuss', 'execute', 'status', 'control', 'clarify']
    execution_request: Literal['none', 'begin', 'continue', 'extend'] | None = Field(
        default='none', description='No execution: omit, null, or "none". Execution: begin, continue, or extend.')
    constraint_change: Literal['retain', 'set_hold', 'release_hold'] = 'retain'
    focus: Literal['current', 'new_scope'] = Field(default='current',
        description='Keep current when accepting/continuing the discussed proposal. new_scope discards the current draft and its proposal; it cannot accept an existing proposal ID.')
    requirement_id: str | None = None
    user_evidence: list[UserEvidence] = Field(default_factory=list, max_length=30)
    scope_summary: str = Field(default='', max_length=30000)
    decision_updates: list[IntentDecision] = Field(default_factory=list, max_length=100)
    open_question_updates: list[IntentQuestionUpdate] = Field(default_factory=list, max_length=100)
    proposal: str | None = Field(default=None, max_length=30000,
        description='Draft proposal text during discussion only. Omit on execute; use accepted_proposal_id or scope_summary instead.')
    accepted_proposal_id: str | None = Field(default=None,
        description='Exact current work_intent.proposal.id when accepting that proposal; requires focus=current. Never invent an ID.')
    control_capability: Literal['task.pause', 'task.resume', 'task.cancel'] | None = None
    control_task_id: str | None = None

    @model_validator(mode='after')
    def coherent(self):
        if self.accepted_proposal_id and self.focus == 'new_scope':
            raise ValueError('ProposalConflict: accepting the existing proposal requires focus=current; new_scope discards it. Keep accepted_proposal_id and use focus=current if the user is proceeding with the discussed plan.')
        if self.execution_request is None:
            self.execution_request = 'none'
        if (self.act == 'execute') != (self.execution_request != 'none'):
            raise ValueError('execute requires begin/continue/extend; other acts cannot request execution')
        if self.constraint_change == 'release_hold' and self.act != 'execute':
            raise ValueError('only an execution request can release a discussion hold')
        if self.act == 'control' and not (self.control_task_id and self.control_capability):
            raise ValueError('control requires an explicit Task and control capability')
        if self.act != 'control' and (self.control_task_id or self.control_capability):
            raise ValueError('Task control fields require act=control; do not attach pause/resume to execute')
        return self


class DiscussionDraftUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    based_on_intent_revision: int = Field(ge=0)
    decision_updates: list[IntentDecision] = Field(default_factory=list, max_length=100)
    open_question_updates: list[IntentQuestionUpdate] = Field(default_factory=list, max_length=100)
    proposal: str | None = Field(default=None, max_length=30000)


TURN_INSTRUCTION = """You are this Project's persistent general-purpose Conversation Agent. You can discuss, inspect, use tools, maintain memory, plan a Workflow, supervise Workers and report delivery. No higher System Main Agent exists. You keep one Project Session across user turns; Column Workers have their own Sessions and responsibilities.

Read-only tools and ordinary discussion need no turn-resolution call. Answer naturally when no business effect is requested. Never update a question-status ledger merely to finish a discussion. Decisions and open issues may be saved as optional memory or a requirement baseline.

Before the first business mutation in a user turn, call conversation.execution.authorize alone with request=begin/continue/extend and the delivery scope when needed. The Host binds the current user message and revision; do not manage a question-status ledger. Use the successful receipt's Requirement/grant for subsequent planning and execution. For an explicit Task pause/resume/cancel, call conversation.task.control.authorize alone with the action and Task ID. Discussion, status, and read-only inspection need no authorization call. A prior assistant promise, a mailbox event, a technical answer or start_task=true never grants execution. An explicit current user request to implement or repair does; do not ask again.

Within an active grant, use Workflow/Task tools to decompose and supervise delivery. One Task is one independently accepted flow unit through its Columns; one Column has one Worker. Mailbox is notification data, not authorization or Worker dialogue. Use explicit Worker input and durable artifacts for coordination. Reuse existing Requirement/Workflow/Task references; do not create duplicate work. Correct failed tool arguments rather than repeating unchanged failures. Report only committed effects and actual Task state.
"""
