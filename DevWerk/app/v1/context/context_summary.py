"""Bounded model views over a separately persisted checkpoint document."""
from __future__ import annotations

import hashlib

from app.v1.protocols.context.context_checkpoint_contracts import (
    ContextSummaryDocument, ContextSummaryView, SummaryEntry, SummarySourceRef,
)
def build_document(messages, *, run_id: str, previous: ContextSummaryDocument | None,
                   previous_checkpoint_id: str | None, max_bytes: int,
                   objective_reference: str = '') -> ContextSummaryDocument:
    entries = list(previous.entries) if previous else []
    for message in messages:
        if message.get('_checkpoint'):
            continue
        identity = message.get('_source_message_id')
        if type(identity) is not int or identity <= 0:
            continue
        role = message.get('role')
        if role not in {'assistant', 'tool'}:
            continue
        calls = message.get('tool_calls') or []
        call_id = message.get('tool_call_id') if role == 'tool' else None
        content = str(message.get('content') or '')
        # Entries are indivisible. A very large source remains retrievable by ID.
        text = content if len(content.encode('utf-8')) <= 4096 else (
            f'{role} message {identity}: full text retained in source message')
        if calls:
            text += ' | tool calls: ' + ', '.join(str(c.get('function', {}).get('name') or '')
                                                  for c in calls)
        entries.append(SummaryEntry(entry_id=f'message-{identity}', kind='observation',
            text=text, source_refs=[SummarySourceRef(agent_run_id=str(message.get('agent_run_id') or run_id),
                message_id=identity, tool_call_id=call_id)]))
    document = ContextSummaryDocument(entries=entries,
        objective_reference=objective_reference or (previous.objective_reference if previous else ''),
        previous_checkpoint_id=previous_checkpoint_id,
        omitted_entry_count=previous.omitted_entry_count if previous else 0)
    while len(document.model_dump_json().encode('utf-8')) > max_bytes and document.entries:
        # Retain recent work; older evidence is still in immutable source tables.
        document.entries.pop(0)
        document.omitted_entry_count += 1
    return document


def project_summary(document: ContextSummaryDocument, checkpoint_id: str,
                    byte_budget: int) -> ContextSummaryView:
    digest = hashlib.sha256(document.model_dump_json().encode('utf-8')).hexdigest()
    selected: list[SummaryEntry] = []
    view = ContextSummaryView(checkpoint_id=checkpoint_id, document_sha256=digest,
        entries=selected, omitted_entry_count=document.omitted_entry_count + len(document.entries))
    for entry in reversed(document.entries):
        candidate = view.model_copy(update={'entries': [entry, *selected],
                                          'omitted_entry_count': document.omitted_entry_count + len(document.entries)-len(selected)-1})
        if len(candidate.model_dump_json().encode('utf-8')) > byte_budget:
            continue
        selected.insert(0, entry)
        view = candidate
    if len(view.model_dump_json().encode('utf-8')) > byte_budget:
        raise ValueError('Checkpoint view budget cannot hold its retrieval reference')
    return view
