"""Validated provider-error log payloads."""

from pydantic import BaseModel, ConfigDict, Field


class ProviderErrorDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    api_name: str
    status_code: int | None = Field(default=None, ge=100, le=599)
    error_code: str
    message: str
    provider_code: int | None = None
    provider_error_type: str | None = None
    request_id: str | None = None
    body_snippet: str | None = None

    def __init__(self, *args, **kwargs):
        # Preserve the positional constructor used by existing Provider adapters
        # and tests while validating the resulting named fields with Pydantic.
        if args:
            field_names = tuple(type(self).model_fields)
            if len(args) > len(field_names):
                raise TypeError(f"expected at most {len(field_names)} positional arguments")
            for name, value in zip(field_names, args):
                if name in kwargs:
                    raise TypeError(f"multiple values for {name!r}")
                kwargs[name] = value
        super().__init__(**kwargs)

    def log_payload(self) -> dict:
        return self.model_dump(mode="json")


class UnexpectedProviderError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_type: str
    message: str
