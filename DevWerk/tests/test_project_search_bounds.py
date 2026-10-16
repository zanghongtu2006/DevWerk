from pathlib import Path

from app.v1.capabilities import CapabilityContext, _files_search
from app.v1.files import ProjectFiles


class _Checks:
    def __init__(self):
        self.calls = 0

    def check(self):
        self.calls += 1


def test_default_project_search_keeps_project_directories_and_bounds_large_files(store, tmp_path):
    project = store.create_project('bounded search', '', str(tmp_path / 'project'))
    root = Path(project['base_dir'])
    for relative, content in {
        'src/LoginTest.java': 'class LoginTest { // target phrase\n}\n',
        'frontend/node_modules/pkg/test.js': '// target phrase\n',
        'frontend/.npm-cache/cache.txt': 'target phrase\n',
        'backend/target/generated.java': '// target phrase\n',
        '.devwerk/internal.txt': 'target phrase\n',
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    (root / 'large.txt').write_text('target phrase\n' + 'x' * 1_000_001, encoding='utf-8')

    files = ProjectFiles(project['base_dir'], store.policy)
    paths = files.search_paths('**/*', limit=1000)
    assert 'src/LoginTest.java' in paths
    assert {'frontend/node_modules/pkg/test.js', 'frontend/.npm-cache/cache.txt',
            'backend/target/generated.java'} <= set(paths)
    assert '.devwerk/internal.txt' not in paths
    assert len(files.search_paths('**/*', limit=1)) == 1
    assert 'frontend/node_modules/pkg/test.js' in files.search_paths(
        'frontend/node_modules/**/*.js', limit=1000)

    checks = _Checks()
    ctx = CapabilityContext(project['id'], project, store, execution_control=checks)
    result = _files_search({'pattern': 'target phrase'}, ctx)
    assert {match['path'] for match in result['matches']} == {
        'src/LoginTest.java', 'frontend/node_modules/pkg/test.js',
        'frontend/.npm-cache/cache.txt', 'backend/target/generated.java'}
    assert checks.calls == len(paths)
