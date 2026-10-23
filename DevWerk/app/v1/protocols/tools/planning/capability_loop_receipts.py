"""Filesystem Loop discovery output contracts."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

from app.v1.protocols.tools.planning.capability_contracts import WorkflowPlanRecord
from app.v1.protocols.tools.planning.capability_project_receipts import WorkflowRevisionReceipt


class LoopSummaryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    loop_key: str = Field(description="Stable Loop key")
    version: str = Field(description="Semantic version")
    name: str = Field(description="Loop name")
    description: str = Field(description="Loop description")
    publisher: str = Field(description="Loop publisher")
    category: str = Field(description="Discovery category")
    tags: list[str] = Field(description="Discovery tags")
    use_case: str = Field(description="Intended use")
    selection_guide: str = Field(description="When to select this Loop")
    input: str = Field(description="Expected input")
    output: str = Field(description="Expected output")
    directory: str = Field(description="Loop directory")

    @classmethod
    def from_record(cls, record: dict) -> "LoopSummaryReceipt":
        return cls(
            loop_key=record["loop_key"], version=record["version"],
            name=record["name"], description=record["description"],
            publisher=record["publisher"], category=record["category"],
            tags=record["tags"], use_case=record["use_case"],
            selection_guide=record["selection_guide"], input=record["input"],
            output=record["output"], directory=record["directory"],
        )


class LoopListReceipt(RootModel[list[LoopSummaryReceipt]]):
    pass


class LoopAssetReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="Asset path relative to the Loop assets directory")
    content: str = Field(description="Asset UTF-8 content")


class LoopInspectReceipt(LoopSummaryReceipt):
    digest: str = Field(pattern=r"^[0-9a-f]{64}$", description="Digest of the Loop card, bundle and assets")
    parameter_schema: dict[str, JsonValue] = Field(description="Externally authored JSON Schema for Loop bindings")
    bundle: dict[str, JsonValue] = Field(description="Externally authored reusable Workflow template")
    meta: str = Field(description="Original Loop metadata text")
    assets: list[LoopAssetReceipt] = Field(description="Referenced Loop assets")

    @classmethod
    def from_parts(
        cls, record: dict, digest: str, parameter_schema: dict,
        bundle: dict, assets: list[LoopAssetReceipt],
    ) -> "LoopInspectReceipt":
        summary = LoopSummaryReceipt.from_record(record)
        return cls(
            loop_key=summary.loop_key, version=summary.version, name=summary.name,
            description=summary.description, publisher=summary.publisher,
            category=summary.category, tags=summary.tags, use_case=summary.use_case,
            selection_guide=summary.selection_guide, input=summary.input,
            output=summary.output, directory=summary.directory,
            digest=digest, parameter_schema=parameter_schema, bundle=bundle,
            meta=record["meta"], assets=assets,
        )


class AppliedLoopIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    loop_key: str = Field(description="Applied Loop key")
    version: str = Field(description="Applied Loop version")
    digest: str = Field(description="Exact Loop package digest")
    name: str = Field(description="Loop name")
    category: str = Field(description="Loop category")
    directory: str = Field(description="Loop directory")


class LoopApplyReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(description="Application ID")
    project_id: str = Field(description="Owning Project ID")
    loop: AppliedLoopIdentity = Field(description="Applied Loop identity")
    bindings: dict[str, JsonValue] = Field(description="Validated Loop parameter values")
    workflow_plan: WorkflowPlanRecord = Field(description="Created Workflow Plan")
    workflow: WorkflowRevisionReceipt = Field(description="Published Workflow revision")
    created_at: str = Field(description="Application timestamp")
