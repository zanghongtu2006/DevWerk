"""Filesystem Loop discovery output contracts."""

from pydantic import BaseModel, ConfigDict, Field, RootModel


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
    digest: str = Field(pattern=r"^[0-9a-f]{64}$", description="Digest of the method guide and resources")
    guide: str = Field(description="Complete descriptive Loop guide; adapt it to the user's Project before authoring a Workflow")
    assets: list[LoopAssetReceipt] = Field(description="Optional descriptive method resources")

    @classmethod
    def from_parts(
        cls, record: dict, digest: str, assets: list[LoopAssetReceipt],
    ) -> "LoopInspectReceipt":
        summary = LoopSummaryReceipt.from_record(record)
        return cls(
            loop_key=summary.loop_key, version=summary.version, name=summary.name,
            description=summary.description, publisher=summary.publisher,
            category=summary.category, tags=summary.tags, use_case=summary.use_case,
            selection_guide=summary.selection_guide, input=summary.input,
            output=summary.output, directory=summary.directory,
            digest=digest, guide=record["meta"], assets=assets,
        )
