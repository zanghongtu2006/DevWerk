"""Conservative adoption of unfinished pre-Operation tool intents."""
import json

from app.v1.domain import AgentToolCall, ToolResult
from app.v1.execution_control import ExecutionReplayUncertain
from app.v1.execution_ledger import operation_sha256


def adopt_legacy_intents(store, registry, spec, current_run_id):
    """Preserve historical facts; block ambiguous effects instead of guessing."""
    scope_id = spec.column_run_id or spec.conversation_job_id
    if not scope_id:
        return
    with store.tx(immediate=True) as db:
        if spec.execution_control:
            spec.execution_control.check()
        runs = db.execute("""SELECT r.id FROM v1_agent_runs r WHERE r.project_id=? AND r.id!=?
                            AND (r.column_run_id=? OR r.conversation_job_id=?) AND r.operation_protocol=0
                            AND NOT EXISTS (SELECT 1 FROM v1_agent_operations o WHERE o.source_run_id=r.id)
                            ORDER BY r.created_at,r.rowid""", (spec.project['id'], current_run_id, scope_id, scope_id)).fetchall()
        for run in runs:
            occurrences = {}
            messages = db.execute("SELECT * FROM v1_agent_messages WHERE agent_run_id=? AND role='assistant' ORDER BY sequence", (run['id'],)).fetchall()
            for message in messages:
                for index, raw in enumerate(json.loads(message['tool_calls_json'] or '[]')):
                    function = raw.get('function') or raw
                    arguments = function.get('arguments') or {}
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                    call = AgentToolCall(id=raw['id'], name=function['name'], arguments=arguments)
                    digest = operation_sha256(call.name, call.arguments)
                    occurrence = occurrences[digest] = occurrences.get(digest, 0)+1
                    if db.execute('SELECT 1 FROM v1_tool_invocations WHERE agent_run_id=? AND tool_call_id=?', (run['id'], call.id)).fetchone():
                        continue
                    key = f'op:{run["id"]}:{message["sequence"]}:{index}'
                    if db.execute('SELECT 1 FROM v1_agent_operations WHERE id=?', (key,)).fetchone():
                        continue
                    candidates = [f'{run["id"]}:{call.id}', f'{scope_id}:effect:{digest}:{occurrence}']
                    receipts = db.execute('SELECT * FROM v1_execution_receipts WHERE project_id=? AND execution_key IN (?,?)', (spec.project['id'], *candidates)).fetchall()
                    if len(receipts) > 1:
                        raise ExecutionReplayUncertain('Legacy intent has ambiguous receipt ownership')
                    receipt = receipts[0] if receipts else None
                    effect = registry.side_effect_kind(call.name)
                    if receipt and receipt['status'] == 'started' and effect in {'write', 'process', 'control'}:
                        raise ExecutionReplayUncertain('Legacy side effect has no terminal receipt; inspect before retrying')
                    if receipt and ':effect:' in receipt['execution_key']:
                        # Old hashes could alias distinct intents across Await.
                        prior = db.execute('SELECT i.arguments_json,i.capability FROM v1_tool_invocations i JOIN v1_agent_runs r ON r.id=i.agent_run_id WHERE r.column_run_id=? AND i.capability=?', (scope_id, call.name)).fetchall()
                        if any(operation_sha256(p['capability'], json.loads(p['arguments_json'])) == digest for p in prior):
                            raise ExecutionReplayUncertain('Legacy hash receipt may belong to another intent; reconciliation required')
                    db.execute('INSERT INTO v1_agent_operations(id,project_id,scope_id,source_run_id,source_sequence,call_index,tool_call_id,capability,arguments_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (key, spec.project['id'], scope_id, run['id'], message['sequence'], index, call.id, call.name, json.dumps(call.arguments), message['created_at']))
                    if receipt and receipt['status'] in {'completed', 'failed'}:
                        result = ToolResult(ok=receipt['status'] == 'completed', capability=call.name,
                                            output=json.loads(receipt['result_json']) if receipt['result_json'] else None,
                                            error={'message': receipt['error']} if receipt['error'] else None,
                                            checkpoint={'execution_key': receipt['execution_key']})
                        if call.name in {'project.command.run', 'system.command.run'} and isinstance(result.output, dict) and result.output.get('exit_code') != 0:
                            result = result.model_copy(update={'ok': False, 'status': 'failed', 'error': {'message': 'Legacy command receipt did not exit successfully'}})
                        store.agents.answer_operation(key, result.model_dump(mode='json'), None, None)
                    elif receipt:
                        raise ExecutionReplayUncertain('Unfinished legacy wait requires its original AwaitHandle')
