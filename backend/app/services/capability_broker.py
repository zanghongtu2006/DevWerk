from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CapabilityOffer:
    capability: str
    provider: str | None = None
    implementation: str | None = None


class CapabilityBroker:
    """Matches workflow capabilities without knowing which product provides them."""

    def offers(self, declaration: object) -> dict[str, CapabilityOffer]:
        if not isinstance(declaration, dict):
            return {}
        raw_items = declaration.get("capabilities")
        if not isinstance(raw_items, list):
            raw_items = declaration.get("tools")
        if not isinstance(raw_items, list):
            return {}

        result: dict[str, CapabilityOffer] = {}
        default_provider = _text(declaration.get("provider"))
        for item in raw_items:
            if isinstance(item, str):
                capability = item.strip()
                provider = default_provider
                implementation = None
            elif isinstance(item, dict):
                capability = _text(item.get("capability") or item.get("name")) or ""
                provider = _text(item.get("provider")) or default_provider
                implementation = _text(item.get("implementation"))
            else:
                continue
            if capability:
                result[capability] = CapabilityOffer(capability, provider, implementation)
        return result

    def available(self, declaration: object, allowed: set[str] | None = None) -> set[str]:
        names = set(self.offers(declaration))
        return names if allowed is None else names.intersection(allowed)

    def resolve(self, declaration: object, capability: str) -> CapabilityOffer | None:
        return self.offers(declaration).get(capability)


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
