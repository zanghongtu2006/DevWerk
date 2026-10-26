"""Test MiniMax thinking-block round trips using harmless synthetic arithmetic.

No DevWerk Project content is read or sent. Returned tool calls are recorded,
never executed. Result files contain response metadata only.
"""

from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path
from typing import Any

import requests

from app.core.config import settings
from app.services.anthropic_client import AnthropicClient


TOOLS = [
    {"name": "calculate", "description": "Add two small integers.",
     "input_schema": {"type": "object", "properties": {"a": {"type": "integer"},
                                                "b": {"type": "integer"}},
                      "required": ["a", "b"], "additionalProperties": False}},
    {"name": "finish", "description": "Record the final arithmetic answer.",
     "input_schema": {"type": "object", "properties": {"answer": {"type": "integer"}},
                      "required": ["answer"], "additionalProperties": False}},
]
USER = {"role": "user", "content": (
    "This is a two-step arithmetic protocol. First check silently that 137 and 289 are "
    "positive integers and their sum lies between 400 and 500. Call calculate once with "
    "a=137 and b=289. After its result, call finish with the answer. Do not answer in prose."
)}


def post(client: AnthropicClient, payload: dict[str, Any], timeout: float) -> tuple[dict[str, Any], dict[str, Any]]:
    headers = {"x-api-key": str(client.api_key), "authorization": f"Bearer {client.api_key}",
               "anthropic-version": "2023-06-01", "content-type": "application/json"}
    started = time.monotonic()
    try:
        response = client.session.post(client.url, json=payload, headers=headers,
                                       timeout=(10, timeout))
    except requests.RequestException as exc:
        return {}, {"transport_error": type(exc).__name__,
                    "seconds": round(time.monotonic() - started, 3)}
    metrics: dict[str, Any] = {"http_status": response.status_code,
                               "seconds": round(time.monotonic() - started, 3)}
    try:
        body = response.json()
    except ValueError:
        return {}, {**metrics, "parse_error": "non_json_response"}
    if not isinstance(body, dict):
        return {}, {**metrics, "parse_error": "non_object_response"}
    blocks = body.get("content") or []
    metrics.update({
        "stop_reason": body.get("stop_reason"),
        "content_types": [block.get("type") for block in blocks if isinstance(block, dict)],
        "tool_names": [block.get("name") for block in blocks
                       if isinstance(block, dict) and block.get("type") == "tool_use"],
        "provider_status_code": (body.get("base_resp") or {}).get("status_code"),
        "usage": {key: (body.get("usage") or {}).get(key) for key in
                  ("input_tokens", "output_tokens", "cache_read_input_tokens")},
    })
    metrics["valid_tool_use"] = metrics["stop_reason"] == "tool_use" and bool(metrics["tool_names"])
    return body, metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--result", type=Path, default=Path(".tmp/minimax-thinking-synthetic.json"))
    args = parser.parse_args()
    if args.trials < 1 or args.timeout <= 0:
        parser.error("trials and timeout must be positive")
    client = AnthropicClient(settings().get_llm_config("column"))
    config = settings().get_llm_config("column")
    seed_payload = {"model": config["model"], "max_tokens": config["max_tokens"],
                    "temperature": config["temperature"], "tools": TOOLS,
                    "messages": [USER]}
    results: list[dict[str, Any]] = []
    for trial in range(1, args.trials + 1):
        print(f"trial={trial} seed started", flush=True)
        seed, seed_metrics = post(client, seed_payload, args.timeout)
        results.append({"trial": trial, "variant": "seed", **seed_metrics})
        print({"trial": trial, "variant": "seed", **{key: seed_metrics.get(key) for key in
            ("http_status", "transport_error", "stop_reason", "content_types", "tool_names", "seconds")}}, flush=True)
        blocks = seed.get("content") or []
        thinking = [block for block in blocks if block.get("type") == "thinking"]
        calls = [block for block in blocks if block.get("type") == "tool_use"]
        if not thinking or len(calls) != 1 or calls[0].get("name") != "calculate":
            results[-1]["pair_not_run"] = "seed_missing_thinking_or_calculate"
            args.result.parent.mkdir(parents=True, exist_ok=True)
            args.result.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            continue
        tool_result = {"role": "user", "content": [{"type": "tool_result",
            "tool_use_id": calls[0]["id"], "content": "426"}]}
        for variant, assistant_blocks in (("omit_thinking", calls), ("preserve_thinking", blocks)):
            payload = copy.deepcopy(seed_payload)
            payload["messages"] = [USER, {"role": "assistant", "content": assistant_blocks}, tool_result]
            print(f"trial={trial} variant={variant} started", flush=True)
            _, metrics = post(client, payload, args.timeout)
            result = {"trial": trial, "variant": variant, **metrics}
            results.append(result)
            print({key: result.get(key) for key in
                   ("trial", "variant", "http_status", "transport_error", "stop_reason",
                    "content_types", "tool_names", "valid_tool_use", "seconds")}, flush=True)
            args.result.parent.mkdir(parents=True, exist_ok=True)
            args.result.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
