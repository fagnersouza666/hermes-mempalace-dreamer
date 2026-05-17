
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'test.yml'


def test_workflow_file_exists():
    assert WORKFLOW.exists(), 'CI workflow .github/workflows/test.yml must exist'


def test_workflow_runs_on_push_and_pull_request():
    spec = yaml.safe_load(WORKFLOW.read_text())
    # PyYAML parses the bare `on:` key as the boolean True.
    triggers = spec.get('on', spec.get(True))
    assert 'push' in triggers
    assert 'pull_request' in triggers


def test_workflow_matrix_covers_python_311_and_312():
    text = WORKFLOW.read_text()
    assert '3.11' in text
    assert '3.12' in text


def test_workflow_runs_pytest():
    text = WORKFLOW.read_text()
    assert 'pip install pytest pyyaml' in text
    assert 'python -m pytest tests -q' in text


def test_workflow_has_explicit_validation_steps():
    spec = yaml.safe_load(WORKFLOW.read_text())
    names = [
        s.get('name', '') for s in spec['jobs']['test']['steps']
    ]
    assert 'Validate plugin.yaml' in names
    assert 'Validate skill frontmatter' in names
    assert 'Import smoke' in names


def test_validation_steps_run_before_pytest():
    spec = yaml.safe_load(WORKFLOW.read_text())
    names = [s.get('name', '') for s in spec['jobs']['test']['steps']]
    assert names.index('Import smoke') < names.index('Run tests')
