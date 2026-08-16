from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests as http_requests


ANTHROPIC_HTTP_ERROR_CODES = {
    400: "LLM_BAD_REQUEST",
    401: "LLM_AUTHENTICATION_ERROR",
    402: "LLM_BILLING_ERROR",
    403: "LLM_PERMISSION_ERROR",
    404: "LLM_NOT_FOUND",
    413: "LLM_REQUEST_TOO_LARGE",
    429: "LLM_RATE_LIMITED",
    500: "LLM_PROVIDER_ERROR",
    504: "LLM_TIMEOUT",
    529: "LLM_OVERLOADED",
}

MINIMAX_ERROR_CODES = {
    1000: ("LLM_PROVIDER_ERROR", "unknown error"),
    1001: ("LLM_TIMEOUT", "request timeout"),
    1002: ("LLM_RATE_LIMITED", "rate limit"),
    1004: ("LLM_AUTHENTICATION_ERROR", "not authorized or token mismatch"),
    1008: ("LLM_BILLING_ERROR", "insufficient balance"),
    1024: ("LLM_PROVIDER_ERROR", "internal error"),
    1026: ("LLM_INPUT_REJECTED", "sensitive input"),
    1027: ("LLM_OUTPUT_REJECTED", "sensitive output"),
    1033: ("LLM_PROVIDER_ERROR", "system error"),
    1039: ("LLM_TOKEN_LIMIT", "token limit"),
    1041: ("LLM_CONCURRENCY_LIMIT", "connection limit"),
    1042: ("LLM_BAD_REQUEST", "invisible or illegal characters"),
    2013: ("LLM_BAD_REQUEST", "invalid parameters"),
    20132: ("LLM_BAD_REQUEST", "invalid sample or voice id"),
    2037: ("LLM_BAD_REQUEST", "voice duration invalid"),
    2039: ("LLM_BAD_REQUEST", "duplicate voice id"),
    2042: ("LLM_PERMISSION_ERROR", "voice id permission denied"),
    2045: ("LLM_RATE_LIMITED", "rate growth limit"),
    2048: ("LLM_BAD_REQUEST", "prompt audio too long"),
    2049: ("LLM_AUTHENTICATION_ERROR", "invalid API key"),
    2056: ("LLM_USAGE_LIMIT", "usage limit exceeded"),
}

RECOVERABLE_LLM_ERROR_CODES = frozenset(
    {
        "LLM_TIMEOUT",
        "LLM_RATE_LIMITED",
        "LLM_OVERLOADED",
        "LLM_PROVIDER_ERROR",
        "LLM_CONCURRENCY_LIMIT",
    }
)


@dataclass
class ProviderErrorDetails:
    provider: str
    api_name: str
    status_code: int | None
    error_code: str
    message: str
    provider_code: int | None = None
    provider_error_type: str | None = None
    request_id: str | None = None
    body_snippet: str | None = None

    def log_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "api_name": self.api_name,
            "status_code": self.status_code,
            "error_code": self.error_code,
            "provider_code": self.provider_code,
            "provider_error_type": self.provider_error_type,
            "request_id": self.request_id,
            "message": self.message,
            "body_snippet": self.body_snippet,
        }


class LLMProviderError(RuntimeError):
    def __init__(self, details: ProviderErrorDetails):
        self.details = details
        super().__init__(details.message)

    @property
    def error_code(self) -> str:
        return self.details.error_code

    @property
    def status_code(self) -> int | None:
        return self.details.status_code

    def user_message(self) -> str:
        parts = [self.details.error_code]
        if self.details.status_code is not None:
            parts.append(f"HTTP {self.details.status_code}")
        if self.details.provider_code is not None:
            parts.append(f"provider code {self.details.provider_code}")
        if self.details.provider_error_type:
            parts.append(self.details.provider_error_type)
        parts.append(self.details.message)
        return ": ".join(parts)


def provider_timeout_error(
    exc: http_requests.Timeout,
    *,
    provider: str,
    api_name: str,
    timeout_seconds: float,
) -> LLMProviderError:
    return LLMProviderError(
        ProviderErrorDetails(
            provider=provider,
            api_name=api_name,
            status_code=None,
            error_code="LLM_TIMEOUT",
            message=f"Provider request exceeded {timeout_seconds:g} seconds: {exc}",
            provider_error_type="timeout_error",
        )
    )


def raise_for_provider_response(
    response: http_requests.Response,
    *,
    provider: str,
    api_name: str,
) -> None:
    if response.status_code < 400:
        return
    raise LLMProviderError(classify_provider_response(response, provider=provider, api_name=api_name))


def raise_for_provider_payload(
    payload: Any,
    *,
    provider: str,
    api_name: str,
    status_code: int | None = None,
    request_id: str | None = None,
) -> None:
    provider_code = _extract_provider_code(payload)
    provider_error_type = _extract_error_type(payload)
    has_error_object = isinstance(payload, dict) and payload.get("error") not in (None, {}, "")
    if provider_code in (None, 0) and not provider_error_type and not has_error_object:
        return
    details = classify_provider_payload(
        payload,
        provider=provider,
        api_name=api_name,
        status_code=status_code,
        request_id=request_id,
    )
    raise LLMProviderError(details)


def classify_provider_response(
    response: http_requests.Response,
    *,
    provider: str,
    api_name: str,
) -> ProviderErrorDetails:
    try:
        body = response.json()
    except ValueError:
        body = None
    body_snippet = response.text
    status_code = int(response.status_code)
    request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
    provider_key = f"{provider} {api_name} {getattr(response, 'url', '')}".lower()
    provider_code = _extract_provider_code(body)
    provider_error_type = _extract_error_type(body)
    provider_message = _extract_message(body) or response.reason or f"HTTP {status_code}"

    if "minimax" in provider_key and provider_code in MINIMAX_ERROR_CODES:
        error_code, default_message = MINIMAX_ERROR_CODES[int(provider_code)]
        message = provider_message if provider_message and provider_message != str(provider_code) else default_message
        return ProviderErrorDetails(
            provider=provider,
            api_name=api_name,
            status_code=status_code,
            error_code=error_code,
            message=message,
            provider_code=int(provider_code),
            provider_error_type=provider_error_type,
            request_id=request_id,
            body_snippet=body_snippet,
        )

    error_code = ANTHROPIC_HTTP_ERROR_CODES.get(
        status_code,
        "LLM_PROVIDER_ERROR" if status_code >= 500 else "LLM_BAD_REQUEST",
    )
    if provider_error_type == "overloaded_error":
        error_code = "LLM_OVERLOADED"
    elif provider_error_type == "rate_limit_error":
        error_code = "LLM_RATE_LIMITED"
    elif provider_error_type == "timeout_error":
        error_code = "LLM_TIMEOUT"

    return ProviderErrorDetails(
        provider=provider,
        api_name=api_name,
        status_code=status_code,
        error_code=error_code,
        message=provider_message,
        provider_code=provider_code,
        provider_error_type=provider_error_type,
        request_id=request_id,
        body_snippet=body_snippet,
    )


def classify_provider_payload(
    payload: Any,
    *,
    provider: str,
    api_name: str,
    status_code: int | None = None,
    request_id: str | None = None,
) -> ProviderErrorDetails:
    provider_key = f"{provider} {api_name}".lower()
    provider_code = _extract_provider_code(payload)
    provider_error_type = _extract_error_type(payload)
    provider_message = _extract_message(payload) or (str(provider_code) if provider_code is not None else "provider error")
    body_snippet = json.dumps(payload, ensure_ascii=False, default=str)

    if "minimax" in provider_key and provider_code in MINIMAX_ERROR_CODES:
        error_code, default_message = MINIMAX_ERROR_CODES[int(provider_code)]
        message = provider_message if provider_message and provider_message != str(provider_code) else default_message
        return ProviderErrorDetails(
            provider=provider,
            api_name=api_name,
            status_code=status_code,
            error_code=error_code,
            message=message,
            provider_code=int(provider_code),
            provider_error_type=provider_error_type,
            request_id=request_id,
            body_snippet=body_snippet,
        )

    error_code = {
        "overloaded_error": "LLM_OVERLOADED",
        "rate_limit_error": "LLM_RATE_LIMITED",
        "timeout_error": "LLM_TIMEOUT",
        "authentication_error": "LLM_AUTHENTICATION_ERROR",
        "permission_error": "LLM_PERMISSION_ERROR",
    }.get(
        provider_error_type,
        "LLM_PROVIDER_ERROR" if status_code is not None and status_code >= 500 else "LLM_BAD_REQUEST",
    )
    return ProviderErrorDetails(
        provider=provider,
        api_name=api_name,
        status_code=status_code,
        error_code=error_code,
        message=provider_message,
        provider_code=provider_code,
        provider_error_type=provider_error_type,
        request_id=request_id,
        body_snippet=body_snippet,
    )


def llm_error_code(exc: BaseException, default: str = "MODEL_ERROR") -> str:
    if isinstance(exc, LLMProviderError):
        return exc.error_code
    return default


def llm_error_message(exc: BaseException) -> str:
    if isinstance(exc, LLMProviderError):
        return exc.user_message()
    return f"{type(exc).__name__}: {exc}"


def llm_error_log_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, LLMProviderError):
        return exc.details.log_payload()
    return {"error_type": type(exc).__name__, "message": str(exc)}


def is_recoverable_llm_error(exc: BaseException) -> bool:
    return isinstance(exc, LLMProviderError) and exc.error_code in RECOVERABLE_LLM_ERROR_CODES


def is_recoverable_llm_error_code(error_code: str | None) -> bool:
    return bool(error_code and error_code in RECOVERABLE_LLM_ERROR_CODES)


def _extract_provider_code(value: Any) -> int | None:
    candidates = [
        value,
        _dig(value, "base_resp"),
        _dig(value, "base_response"),
        _dig(value, "error"),
        _dig(value, "detail"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("status_code", "code", "error_code", "err_code"):
            parsed = _parse_int(candidate.get(key))
            if parsed is not None:
                return parsed
    return None


def _extract_error_type(value: Any) -> str | None:
    err = _dig(value, "error")
    if isinstance(err, dict):
        text = err.get("type") or err.get("code")
        return str(text) if text else None
    if isinstance(value, dict):
        text = value.get("type") or value.get("error_type")
        if text:
            normalized = str(text).strip().lower()
            # Successful Anthropic-compatible responses use type="message".
            # Only explicit error-shaped top-level types are error evidence.
            if normalized.endswith("_error") or normalized in {
                "error",
                "authentication_error",
                "permission_error",
                "rate_limit_error",
                "overloaded_error",
                "timeout_error",
            }:
                return normalized
    return None


def _extract_message(value: Any) -> str | None:
    if isinstance(value, dict):
        err = value.get("error")
        if isinstance(err, dict):
            for key in ("message", "status_msg", "msg"):
                if err.get(key):
                    return str(err[key])
        for key in ("message", "status_msg", "msg", "error_msg"):
            if value.get(key):
                return str(value[key])
        base = value.get("base_resp") or value.get("base_response")
        if isinstance(base, dict):
            for key in ("status_msg", "message", "msg"):
                if base.get(key):
                    return str(base[key])
    return None


def _dig(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _parse_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _safe_text(value: str | None, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"
