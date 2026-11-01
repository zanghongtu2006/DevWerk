"""Request-local subscriber for public provider text blocks."""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pydantic_core import from_json


_subscriber: ContextVar[Callable[[str], None] | None] = ContextVar("visible_text", default=None)
_public_field: ContextVar[tuple[str, str, Callable[[str], None]] | None] = ContextVar("visible_tool_field", default=None)


@contextmanager
def listen_for_visible_text(callback: Callable[[str], None], *, public_field: tuple[str, str, Callable[[str], None]] | None = None) -> Iterator[None]:
    token = _subscriber.set(callback)
    field_token = _public_field.set(public_field)
    try:
        yield
    finally:
        _subscriber.reset(token)
        _public_field.reset(field_token)


def publish_visible_text(text: str) -> None:
    callback = _subscriber.get()
    if callback is not None and text:
        callback(text)


def publish_tool_preview(tool_name: str, parts: list[str], size: int) -> None:
    target = _public_field.get()
    if target is None or target[0] != tool_name or size > 32768:
        return
    try:
        partial = from_json("".join(parts), allow_partial="trailing-strings")
    except ValueError:
        return
    if isinstance(partial, dict) and isinstance(partial.get(target[1]), str):
        target[2](partial[target[1]])
