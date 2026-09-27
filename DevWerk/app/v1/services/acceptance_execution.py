"""Execute frozen acceptance and bind fresh scenario assertions to test sources."""
import hashlib
import json

from app.v1.domain import ToolResult


def run_acceptance_check(registry, context, check):
    path = check.get('report_path')
    target = context.files.resolve(path) if path else None
    before = target.stat().st_mtime_ns if target and target.is_file() else None
    result = registry.dispatch(check['capability'],check['arguments'],context)
    if not result.ok or not target:
        return result
    try:
        if not target.is_file() or target.stat().st_mtime_ns == before:
            raise ValueError('Acceptance report was not freshly written by this execution')
        if target.stat().st_size > 2_000_000:
            raise ValueError('Acceptance report is too large')
        data = target.read_bytes()
        report = json.loads(data)
        if not isinstance(report, dict) or report.get('schema_version') != 'devwerk.acceptance-report.v1':
            raise ValueError('Acceptance report schema_version must be devwerk.acceptance-report.v1')
        scenarios = report.get('scenarios')
        if not isinstance(scenarios,list) or not scenarios:
            raise ValueError('Acceptance report has no scenarios')
        by_id = {}
        for item in scenarios:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str):
                raise ValueError('Report scenario must have a string id')
            identity = item['id']
            if identity in by_id:
                raise ValueError('Duplicate report scenario: '+identity)
            by_id[identity] = item
        verified = []
        for identity in check['scenario_ids']:
            item = by_id.get(identity)
            if not item or item.get('status') != 'passed':
                raise ValueError('Required scenario missing, skipped or failed: '+identity)
            assertions = item.get('assertions')
            if not isinstance(assertions,list) or not assertions:
                raise ValueError('Scenario has no observed assertions: '+identity)
            if any(not isinstance(a, dict) or 'actual' not in a or 'expected' not in a or
                   type(a['actual']) is not type(a['expected']) or a['actual'] != a['expected'] for a in assertions):
                raise ValueError('Scenario assertion mismatch: '+identity)
            source = item['test_source']
            if not isinstance(source, dict):
                raise ValueError('Scenario requires a test_source object')
            source_path = context.files.resolve(source['path'])
            actual_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if source.get('sha256') != actual_hash:
                raise ValueError('Scenario test source changed or is not bound: '+identity)
            verified.append({'id':identity,'test_source':source,'assertions':assertions})
        output = dict(result.output or {})
        output['scenario_evidence'] = {'execution_key':context.execution_key,'report_path':path,
            'report_sha256':hashlib.sha256(data).hexdigest(),'scenarios':verified}
        return result.model_copy(update={'output':output})
    except (ValueError,KeyError,TypeError,OSError) as exc:
        return ToolResult(ok=False,capability=check['capability'],output=result.output,
            error={'type':'AcceptanceEvidenceInvalid','message':str(exc)},
            checkpoint={'failure_disposition':'verification_failed','execution_key':context.execution_key})
