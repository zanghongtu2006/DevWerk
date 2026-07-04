from __future__ import annotations

from app.services.anthropic_client import AnthropicClient


def test_anthropic_parser_accepts_repeated_json_objects() -> None:
    first = '{"reply":"ok","workflow":{"columns":[{"status_key":"todo","title":"Todo"}]}}'
    repeated = first + "\n" + first

    parsed = AnthropicClient._parse_json_object(repeated)

    assert parsed["reply"] == "ok"
    assert parsed["workflow"]["columns"][0]["status_key"] == "todo"
    assert "raw_text" not in parsed


def test_anthropic_parser_accepts_json_with_trailing_text() -> None:
    parsed = AnthropicClient._parse_json_object('{"reply":"ok","done":true}\nextra explanation')

    assert parsed == {"reply": "ok", "done": True}


def test_anthropic_parser_repairs_missing_trailing_closers() -> None:
    parsed = AnthropicClient._parse_json_object(
        '{"reply":"ok","workflow":{"columns":[{"status_key":"todo","title":"Todo"}],"actions":{"workflow_done":{"to":"done"}}}'
    )

    assert parsed["reply"] == "ok"
    assert parsed["workflow"]["columns"][0]["title"] == "Todo"


def test_anthropic_parser_repairs_final_wrong_closer() -> None:
    parsed = AnthropicClient._parse_json_object(
        '{"reply":"ok","notes":["one","two"}'
    )

    assert parsed == {"reply": "ok", "notes": ["one", "two"]}
