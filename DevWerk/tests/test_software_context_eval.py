import asyncio
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from scripts import eval_software_context as evaluation


def test_evaluation_wait_does_not_spend_execution_steps():
    task = {'id':'task','status':'recovering','control_state':'active','current_column':'backend',
            'state_version':1,'next_retry_at':(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()}
    elapsed, waits, calls = [0], [], []
    async def sleep(seconds):
        assert seconds <= 30
        waits.append(seconds)
        elapsed[0] += seconds
        task['next_retry_at'] = None
    def step(task_id):
        calls.append(task_id)
        if len(calls) == 1:
            return  # Lease/scheduler has not admitted execution yet.
        task.update(status='done',state_version=2)
    store = SimpleNamespace(list_tasks=lambda _: [dict(task)],get_task=lambda _:dict(task))
    report = {'steps':[]}
    asyncio.run(evaluation.deliver(store,SimpleNamespace(step=step),'project',report,lambda:None,
        max_steps=1,sleep=sleep,clock=lambda:elapsed[0]))
    assert len(waits) == 2 and len(calls) == 2
    assert len(report['steps']) == 1 and report['steps'][0]['status'] == 'done'


def test_evaluation_no_progress_has_an_independent_time_bound():
    task = {'id':'task','status':'waiting','control_state':'active','current_column':'work','state_version':1}
    elapsed = [0]
    async def sleep(seconds):
        elapsed[0] += seconds
    store = SimpleNamespace(list_tasks=lambda _: [dict(task)],get_task=lambda _:dict(task))
    report = {'steps':[]}
    with pytest.raises(TimeoutError,match='wall-clock'):
        asyncio.run(evaluation.deliver(store,SimpleNamespace(step=lambda _:None),'project',report,lambda:None,
            time_limit=10,sleep=sleep,clock=lambda:elapsed[0]))
    assert report['steps'] == []


def test_resume_rejects_production_and_mismatched_project(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation,'ROOT',tmp_path)
    with pytest.raises(ValueError,match='isolated'):
        evaluation.resume_evidence(tmp_path/'data')
    evidence = tmp_path/'data/evals/case'
    evidence.mkdir(parents=True)
    report = {'project_id':'project'}
    (evidence/'report.json').write_text(json.dumps(report))
    with sqlite3.connect(evidence/'runtime.db') as db:
        db.execute('CREATE TABLE v1_projects(id TEXT,base_dir TEXT)')
        db.execute('INSERT INTO v1_projects VALUES(?,?)',('project',str(tmp_path/'production')))
    with pytest.raises(ValueError,match='does not match'):
        evaluation.resume_evidence(evidence)
    with sqlite3.connect(evidence/'runtime.db') as db:
        db.execute('UPDATE v1_projects SET base_dir=?',(str(evidence/'project'),))
    assert evaluation.resume_evidence(evidence) == (evidence.resolve(),report)
