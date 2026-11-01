"""Checkpoint read boundary; summaries are references, not execution receipts."""
import hashlib

from app.v1.context.context_projection import slice_source_text
from app.v1.protocols.context.context_checkpoint_contracts import ContextSummaryDocument
from app.v1.protocols.tools.agent.capability_checkpoint_contracts import (
    CheckpointReadInput, CheckpointReadReceipt,
)


def read_checkpoint(args, ctx):
    request = CheckpointReadInput.model_validate(args)
    row = ctx.store.agents.get_context_checkpoint(ctx.project_id, request.checkpoint_id)
    if not row:
        raise ValueError('Checkpoint absent or outside this Project')
    if ctx.column_run_id:
        with ctx.store.connect() as db:
            owner = db.execute('SELECT agent_instance_id FROM v1_agent_runs WHERE id=? AND project_id=?',
                               (row['agent_run_id'], ctx.project_id)).fetchone()
        if not owner or owner['agent_instance_id'] != ctx.agent_instance_id:
            raise ValueError('Checkpoint outside this Agent scope')
    legacy = not bool(row.get('document_json'))
    if legacy:
        if request.entry_id:
            raise ValueError('Legacy checkpoint has no entry IDs')
        content = row['summary_json']
    else:
        document = ContextSummaryDocument.model_validate_json(row['document_json'])
        if request.entry_id:
            entry = next((item for item in document.entries if item.entry_id == request.entry_id), None)
            if entry is None:
                raise ValueError('Checkpoint entry absent')
            content = entry.model_dump_json()
        else:
            content = document.model_dump_json()
    chunk, next_offset = slice_source_text(content, offset=request.offset,
        max_characters=request.limit, max_bytes=12_000)
    return CheckpointReadReceipt(checkpoint_id=request.checkpoint_id,
        entry_id=request.entry_id, legacy=legacy, content=chunk,
        offset=request.offset, total_characters=len(content), next_offset=next_offset,
        sha256=hashlib.sha256(content.encode('utf-8')).hexdigest()).model_dump(mode='json')
