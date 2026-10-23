"""Small provider references to immutable execution evidence.

The runtime ledger remains complete. These views never authorize completion.
"""
from __future__ import annotations

import json
import hashlib
from typing import Any


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str).encode('utf-8'))


def _short(value: Any, limit: int = 180) -> str:
    text = str(value or '')
    return text if len(text) <= limit else text[:limit] + '…'


def project_ledger(entries: list[dict[str, Any]], *, max_bytes: int = 6000) -> dict[str, Any]:
    """Prioritize unresolved failures, then recent receipts, without raw facts."""
    from app.v1.execution.execution_ledger import unresolved_execution_failures

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
            for key in ('exit_code', 'path'):
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


def slice_source_text(text: str, *, offset: int, max_characters: int,
                      max_bytes: int) -> tuple[str, int | None]:
    """Return a contiguous page; its cursor always follows the returned text."""
    if offset < 0 or offset > len(text):
        raise ValueError('Source offset is outside the content')
    if max_characters < 0 or max_bytes < 0 or (max_characters > 0 and max_bytes == 0):
        raise ValueError('Source page limits must be positive')
    end = min(len(text), offset + max_characters)
    page = text[offset:end]
    while page and len(page.encode('utf-8')) > max_bytes:
        if len(page) == 1:
            raise ValueError('Source byte budget cannot fit the next character')
        page = page[:max(1, len(page)//2)]
    next_offset = offset + len(page)
    return page, next_offset if next_offset < len(text) else None


def project_handoff(snapshot: dict[str, Any], *, max_bytes: int = 2500) -> dict[str, Any]:
    """Offer a bounded reference without asserting that an excerpt is complete."""
    raw = json.dumps(snapshot['summary'], ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'))
    view = {'snapshot_id': snapshot['id'], 'revision': snapshot['revision'],
            'through_message_id': snapshot['through_message_id'],
            'sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest(),
            'summary': snapshot['summary'], 'content_complete': True,
            'instruction': 'Historical reference only; the current Assignment defines the work.'}
    if _size(view) <= max_bytes:
        return view
    view['content_complete'] = False
    view.pop('summary')
    view['retrieve'] = 'agent.context.read with snapshot_id and offset'
    size_left = max(0, max_bytes - _size(view) - 30)
    view['summary_excerpt'], _ = slice_source_text(raw, offset=0,
        max_characters=size_left, max_bytes=size_left)
    return view
