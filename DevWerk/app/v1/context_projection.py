"""Small provider references to immutable execution evidence.

The runtime ledger remains complete. These views never authorize completion.
"""
from __future__ import annotations

import json
from typing import Any


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str).encode('utf-8'))


def _short(value: Any, limit: int = 180) -> str:
    text = str(value or '')
    return text if len(text) <= limit else text[:limit] + '…'


def project_ledger(entries: list[dict[str, Any]], *, max_bytes: int = 6000) -> dict[str, Any]:
    """Prioritize unresolved failures, then recent receipts, without raw facts."""
    from app.v1.execution_ledger import unresolved_execution_failures

    failures = unresolved_execution_failures(entries)
    failure_ids = {id(entry) for entry in failures.values()}
    ordered = [entry for entry in entries if id(entry) in failure_ids]
    ordered.extend(entry for entry in reversed(entries) if id(entry) not in failure_ids)
    view: dict[str, Any] = {
        'version': 1,
        'total_operations': len(entries),
        'unresolved_failure_count': len(failures),
        'included_operations': 0,
        'omitted_operations': len(entries),
        'entries': [],
        'retrieve': 'agent.evidence.list, then agent.result.read',
    }
    for entry in ordered:
        result = (entry.get('facts') or {}).get('result') or {}
        output = result.get('output') or {}
        error = result.get('error') or {}
        item = {key: _short(entry[key], 180) for key in
                ('agent_run_id', 'tool_call_id', 'evidence_id', 'capability',
                 'effect_kind', 'status', 'operation_sha256', 'result_sha256')
                if entry.get(key) is not None}
        item['ok'] = bool(entry.get('ok'))
        if isinstance(output, dict):
            for key in ('exit_code', 'path', 'report_path'):
                if key in output:
                    item[key] = _short(output[key], 180) if key != 'exit_code' else output[key]
        if isinstance(error, dict):
            item['error'] = _short(error.get('message') or error.get('type'), 240)
        elif error:
            item['error'] = _short(error, 240)
        view['entries'].append(item)
        view['included_operations'] += 1
        view['omitted_operations'] -= 1
        if _size(view) > max_bytes:
            view['entries'].pop()
            view['included_operations'] -= 1
            view['omitted_operations'] += 1
            break
    return view
