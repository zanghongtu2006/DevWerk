import json
import sys

import pytest

from app.v1.command_resolution import CommandNotFound
from app.v1.policy import ExecutionLimits
from app.v1.process_runner import run_command


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows batch launcher')
@pytest.mark.parametrize('prefix',['./','.\\'])
def test_explicit_current_directory_shim_uses_project_cwd(tmp_path, prefix):
    project = tmp_path/'project with spaces'
    project.mkdir()
    (project/'local-shim.cmd').write_text('@echo off\r\necho shim-ok %1\r\nexit /b 7\r\n')
    result = run_command([prefix+'local-shim.cmd','a & b'],project,ExecutionLimits())
    assert result['exit_code'] == 7
    assert 'shim-ok "a & b"' in result['stdout']


def test_native_unicode_quotes_and_metacharacters_remain_literal(tmp_path):
    arguments = ['中文 路径','say "yes"','a & b','%literal%!','尾部\\']
    result = run_command([sys.executable,'-X','utf8','-c',
        'import json,sys; print(json.dumps(sys.argv[1:],ensure_ascii=False))',*arguments],tmp_path,ExecutionLimits())
    assert result['exit_code'] == 0
    assert json.loads(result['stdout']) == arguments


def test_missing_explicit_command_rejects_before_process_admission(tmp_path):
    from contextlib import contextmanager
    admitted = []
    @contextmanager
    def guard():
        admitted.append(True)
        yield
    with pytest.raises(CommandNotFound):
        run_command(['./does-not-exist-devwerk-eval.cmd'],tmp_path,ExecutionLimits(),guard=guard)
    assert admitted == []


def test_missing_system_command_does_not_leave_uncertain_execution(store, tmp_path):
    from app.v1.capabilities import CapabilityContext

    project = store.create_project('missing system command', '', str(tmp_path / 'project'))
    result = store.registry.dispatch(
        'system.command.run',
        {'argv': ['./does-not-exist-devwerk-eval.cmd'], 'cwd': project['base_dir']},
        CapabilityContext(project['id'], project, store, agent_run_id='arun_test'),
    )

    assert not result.ok
    assert result.error['type'] == 'CommandNotFound'
    assert result.checkpoint['failure_disposition'] == 'rejected_before_effect'
    with store.connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM v1_execution_receipts WHERE project_id=? AND capability='system.command.run'",
            (project['id'],),
        ).fetchone()[0] == 0
