"""Tool boundary for Conversation Job work records."""
from app.v1.protocols.conversation.conversation_work_contracts import ConversationWorkReadInput


def update_work(args, ctx):
    return ctx.store.work_progress.update(ctx, args).model_dump(mode='json')


def read_work(args, ctx):
    request = ConversationWorkReadInput.model_validate(args)
    job = ctx.store.agents.assert_conversation(ctx)
    selected = request.job_id or job['id']
    snapshot = ctx.store.work_progress.current_view(ctx.project_id, selected)
    return {'snapshot': snapshot.model_dump(mode='json') if snapshot else None,
            'reference_only': selected != job['id']}
