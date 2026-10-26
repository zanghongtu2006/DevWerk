"""Persistence shape for bindings on already frozen legacy Workflows."""

from pydantic import JsonValue, RootModel


class LegacyLoopBindingSnapshot(RootModel[dict[str, JsonValue]]):
    """Preserve historical binding values without exposing them as a new Tool input."""
