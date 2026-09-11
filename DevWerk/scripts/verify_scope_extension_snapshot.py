"""Read-only source DB -> disposable SQLite copy; never executes novel Workers."""
import argparse
import json
from pathlib import Path
import sqlite3
import tempfile
import sys
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.v1.store import V1Store
from app.v1.capabilities import CapabilityContext, build_core_registry
from app.v1.policy import PlatformPolicyLoader
from app.v1.domain import TaskPlan


def verify(source, project_id):
    with tempfile.TemporaryDirectory(prefix='devwerk-scope-audit-') as directory:
        target = Path(directory) / 'snapshot.db'
        with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True)) as original, closing(sqlite3.connect(target)) as copy:
            original.backup(copy)
        store = V1Store(str(target), registry=build_core_registry())
        store.register_platform_policy(PlatformPolicyLoader(Path(__file__).resolve().parents[1] / 'DEVWERK.md').load())
        # These changes exist only in the disposable copy. No running source
        # process, live job or workspace is touched.
        with store.tx(immediate=True) as db:
            db.execute('UPDATE v1_projects SET base_dir=? WHERE id=?', (str(Path(directory) / 'project'), project_id))
            db.execute('UPDATE v1_conversation_agents SET lease_owner=NULL,lease_until=NULL WHERE project_id=?', (project_id,))
        project = store.get_project(project_id)
        old_tasks = store.list_tasks(project_id)
        assert len(old_tasks) == 6 and all(t['status'] == 'done' for t in old_tasks)
        old_workflow = store.get_workflow(project_id)
        old_binding = store.get_project_loop_binding(project_id, old_workflow['id'])
        requirement = next(r for r in store.agents.list_requirements(project_id) if r['scope_key'] == 'default')
        job = store.create_conversation_job(project_id, 'Extend chapters 7–12 without ending the story', True)
        job = store.claim_conversation_job(job['id'], 'snapshot-auditor')
        assert job, 'A different persisted job in the source snapshot prevents this simulation'
        main = store.agents.main(project_id)
        run = store.begin_agent_run(project_id=project_id, kind='conversation', instruction_revision=1,
                                    instruction_snapshot='snapshot validation', context_snapshot={}, capabilities=[],
                                    platform_policy=store.latest_platform_policy(), runtime_policy=store.policy,
                                    conversation_job_id=job['id'])
        ctx = CapabilityContext(project_id, project, store, agent_run_id=run['id'], agent_instance_id=main['id'],
                                requirement_id=requirement['id'], requirement_revision=requirement['revision'],
                                user_initiated=True)
        args = {'requirement_id': requirement['id'], 'expected_revision': requirement['revision'],
                'expected_workflow_revision_id': old_workflow['id'], 'objective': 'Extend to chapter twelve; preserve the open story',
                'binding_patch': {'chapter_count': 12, 'ending_policy': 'continue'},
                'loop_digest': store.get_loop('novel.production')['digest'], 'reason': 'User explicitly requested continuation'}
        result = store.registry.dispatch('project.scope.revise', args, ctx)
        assert result.ok, result.error
        revision = result.output
        old_plan = store.get_task_plan(project_id, old_tasks[0]['task_plan_id'])['plan']
        template = next(t for t in old_plan['tasks'] if t['input']['chapter_number'] == 6)
        new_items = []
        for n in range(7, 13):
            item = json.loads(json.dumps(template))
            item.update(proposed_task_ref=f'chapter{n:02}', title=f'Chapter {n}', objective=f'Continue chapter {n}',
                        brief='Continue accepted history and leave the long story open',
                        dependencies=[f'chapter{n-1:02}'] if n > 7 else [])
            item['input'] = {**item['input'], 'chapter_number': n,
                             'summary_path': f'summaries/chapter{n:02}.md', 'body_path': f'chapters/chapter{n:02}.md',
                             'review_path': f'reviews/chapter{n:02}.md'}
            new_items.append(item)
        plan = TaskPlan.model_validate({**old_plan, 'objective': 'Continue chapters 7–12',
                                      'workflow_revision_id': revision['workflow_revision_id'], 'tasks': new_items})
        saved = store.registry.dispatch('task.plan.save', {'plan': plan.model_dump(mode='json')}, ctx)
        assert saved.ok, saved.error
        created = store.registry.dispatch('task.create', {'task_plan_id': saved.output['id'], 'proposed_task_ref': 'chapter07'}, ctx)
        assert created.ok, created.error
        all_tasks = store.list_tasks(project_id)
        assert len(all_tasks) == 12
        assert all(store.get_task(t['id']) == t for t in old_tasks), 'Historical Task mutated'
        assert store.get_project_loop_binding(project_id, old_workflow['id']) == old_binding
        assert revision['bindings']['chapter_max_characters'] == old_binding['bindings']['chapter_max_characters']
        return {'source_open_mode': 'read-only', 'project_id': project_id, 'old_requirement_status': requirement['status'],
                'old_scope_revision': requirement['revision'], 'new_scope_revision': revision['requirement']['revision'],
                'old_loop_version': old_binding['loop_version'], 'new_loop_version': store.get_project_loop_binding(project_id)['loop_version'],
                'historical_tasks_unchanged': len(old_tasks), 'additional_tasks': len(all_tasks)-len(old_tasks),
                'chapter_max_characters': revision['bindings']['chapter_max_characters'], 'ending_policy': revision['bindings']['ending_policy'],
                'workers_executed': 0, 'source_mutated': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('data/devwerk.db'))
    parser.add_argument('--project', default='prj_22d5638974ee405885b0552e4a24e91f')
    args = parser.parse_args()
    print(json.dumps(verify(args.source, args.project), ensure_ascii=False, indent=2))
