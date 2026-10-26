"""Replay logged MiniMax read timeouts as SSE without executing tool calls.

Example: python tools/minimax_stream_timeout_demo.py --trace llm_... --result .tmp/stream.json
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests

from app.core.config import settings
from app.services.anthropic_client import AnthropicClient


REQUEST_EVENT = "llm.provider_request "
ERROR_EVENT = "llm.agent_error "
TERMINAL_EVENT = "message_stop"


def logged_timeout_payloads(log_path: Path, trace_ids: list[str]) -> dict[str, dict[str, Any]]:
    wanted = set(trace_ids)
    payloads: dict[str, dict[str, Any]] = {}
    errors: set[str] = set()
    decoder = json.JSONDecoder()
    with log_path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if REQUEST_EVENT in line:
                event = REQUEST_EVENT
            elif ERROR_EVENT in line:
                event = ERROR_EVENT
            else:
                continue
            try:
                item, _ = decoder.raw_decode(line.split(event, 1)[1])
            except (ValueError, IndexError):
                continue
            trace_id = item.get("trace_id")
            if trace_id not in wanted:
                continue
            if event == REQUEST_EVENT:
                payloads[trace_id] = item["payload"]
            elif item.get("error_type") == "LLM_TIMEOUT":
                errors.add(trace_id)
    missing = wanted - payloads.keys()
    not_timeout = wanted - errors
    if missing or not_timeout:
        raise ValueError(f"Missing provider payloads: {sorted(missing)}; not logged timeouts: {sorted(not_timeout)}")
    return payloads


def replay(client: AnthropicClient, trace_id: str, payload: dict[str, Any], *,
           read_idle_seconds: float, overall_seconds: float) -> dict[str, Any]:
    request = {**payload, "stream": True}
    metrics: dict[str, Any] = {
        "source_trace_id": trace_id,
        "model": request.get("model"),
        "utf8_json_bytes": len(json.dumps(request, ensure_ascii=False).encode("utf-8")),
        "http_body_bytes": len(requests.Request("POST", client.url, json=request).prepare().body),
        "stream": True,
        "read_idle_seconds": read_idle_seconds,
        "overall_seconds": overall_seconds,
        "status": "incomplete",
        "events": {},
        "delta_characters": {},
    }
    started = time.monotonic()
    headers = {
        "x-api-key": str(client.api_key),
        "authorization": f"Bearer {client.api_key}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        "accept": "text/event-stream",
    }
    counts: Counter[str] = Counter()
    deltas: Counter[str] = Counter()
    last_event_at: float | None = None
    try:
        with client.session.post(
            client.url, json=request, headers=headers,
            timeout=(10, read_idle_seconds), stream=True,
        ) as response:
            metrics["headers_seconds"] = round(time.monotonic() - started, 3)
            metrics["http_status"] = response.status_code
            metrics["request_id"] = response.headers.get("request-id") or response.headers.get("x-request-id")
            if response.status_code >= 400:
                metrics["status"] = "http_error"
                return metrics
            name = ""
            data_lines: list[str] = []
            for raw_line in response.iter_lines(chunk_size=1):
                now = time.monotonic()
                if now - started > overall_seconds:
                    metrics["status"] = "overall_deadline"
                    break
                line = raw_line.decode("utf-8", errors="replace")
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif line == "" and (name or data_lines):
                    event_name = name
                    try:
                        data = json.loads("\n".join(data_lines)) if data_lines else {}
                    except ValueError:
                        data = {}
                    name, data_lines = "", []
                    counts[event_name] += 1
                    if last_event_at is None:
                        metrics["first_event_seconds"] = round(now - started, 3)
                    else:
                        metrics["max_event_gap_seconds"] = round(max(
                            metrics.get("max_event_gap_seconds", 0), now - last_event_at), 3)
                    last_event_at = now
                    if event_name == "content_block_delta":
                        delta = data.get("delta") or {}
                        kind = str(delta.get("type") or "unknown")
                        value = delta.get("text") or delta.get("thinking") or delta.get("partial_json") or ""
                        if value and "first_content_seconds" not in metrics:
                            metrics["first_content_seconds"] = round(now - started, 3)
                        deltas[kind] += len(value)
                    elif event_name == "message_delta":
                        metrics["stop_reason"] = (data.get("delta") or {}).get("stop_reason")
                        metrics["usage"] = data.get("usage")
                    elif event_name == "error":
                        metrics["status"] = "stream_error"
                        error = data.get("error") or {}
                        metrics["provider_error_type"] = error.get("type") if isinstance(error, dict) else None
                        break
                    elif event_name == TERMINAL_EVENT:
                        metrics["status"] = "complete"
                        break
            metrics["events"] = dict(counts)
            metrics["delta_characters"] = dict(deltas)
    except requests.Timeout as exc:
        metrics["status"] = "read_timeout"
        metrics["exception_type"] = type(exc).__name__
    except requests.RequestException as exc:
        metrics["status"] = "transport_error"
        metrics["exception_type"] = type(exc).__name__
    finally:
        metrics["total_seconds"] = round(time.monotonic() - started, 3)
        metrics["events"] = dict(counts)
        metrics["delta_characters"] = dict(deltas)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", action="append", required=True, help="Logged LLM timeout trace ID")
    parser.add_argument("--log", type=Path, default=Path("data/logs/devwerk.log"))
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--read-idle", type=float, default=90)
    parser.add_argument("--overall", type=float, default=720)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.read_idle <= 0 or args.overall <= 0:
        parser.error("timeouts must be positive")
    payloads = logged_timeout_payloads(args.log, args.trace)
    results: list[dict[str, Any]] = []
    client = None if args.dry_run else AnthropicClient(settings().get_llm_config("column"))
    for trace_id in args.trace:
        payload = payloads[trace_id]
        if args.dry_run:
            result = {"source_trace_id": trace_id, "model": payload.get("model"),
                      "utf8_json_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                      "http_body_bytes": len(requests.Request("POST", "https://example.invalid", json=payload).prepare().body),
                      "messages": len(payload.get("messages") or []),
                      "tools": len(payload.get("tools") or [])}
        else:
            result = replay(client, trace_id, payload,
                            read_idle_seconds=args.read_idle, overall_seconds=args.overall)
        results.append(result)
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
