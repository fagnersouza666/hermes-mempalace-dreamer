
from pathlib import Path
import importlib.util
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]

class FakeContext:
    def __init__(self):
        self.skills = []
        self.cli_commands = []
    def register_skill(self, name, path, description=''):
        self.skills.append((name, Path(path), description))
    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=''):
        self.cli_commands.append({
            'name': name,
            'help': help,
            'setup_fn': setup_fn,
            'handler_fn': handler_fn,
            'description': description,
        })

def load_plugin():
    spec = importlib.util.spec_from_file_location('plugin_under_test', ROOT / '__init__.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['plugin_under_test'] = module
    spec.loader.exec_module(module)
    return module

def test_plugin_registers_skill_and_cli_command():
    module = load_plugin()
    ctx = FakeContext()
    module.register(ctx)
    assert ctx.skills, 'plugin must register a bundled skill'
    assert ctx.skills[0][0] == 'mempalace-dreaming'
    assert ctx.skills[0][1].exists()
    assert any(c['name'] == 'mempalace-dreaming' for c in ctx.cli_commands)

def test_skill_is_mempalace_first_and_not_builtin_fallback():
    skill = ROOT / 'skills' / 'mempalace-dreaming' / 'SKILL.md'
    text = skill.read_text()
    assert text.startswith('---')
    end = text.find('\n---\n', 3)
    fm = yaml.safe_load(text[3:end])
    assert fm['name'] == 'mempalace-dreaming'
    assert 'mempalace_search' in text
    assert 'mempalace_remember' in text
    assert 'unknown backend fallback is report-only' in text.lower()
    assert 'fallback to built-in' not in text.lower()

def test_setup_plan_is_dry_run_safe(tmp_path):
    module = load_plugin()
    plan = module.build_setup_plan(hermes_home=tmp_path, schedule_dreaming=True, time='05:30')
    assert plan['config']['memory.provider'] == 'mempalace'
    assert plan['config']['memory.memory_enabled'] is True
    assert plan['config']['memory.user_profile_enabled'] is True
    assert plan['schedule']['time'] == '05:30'
    assert all(str(tmp_path) in p for p in plan['directories'])


def test_setup_plan_supports_extraction_profile_mode(tmp_path):
    module = load_plugin()
    plan = module.build_setup_plan(
        hermes_home=tmp_path,
        profile_mode='extraction',
    )
    assert plan['profile_mode'] == 'extraction'
    assert plan['config']['memory.provider'] == 'mempalace'
    assert plan['config']['memory.memory_enabled'] is False
    assert plan['config']['memory.user_profile_enabled'] is False
    assert any('stock' in note.lower() for note in plan['notes'])


def test_cli_setup_plan_prints_json(capsys, tmp_path):
    import argparse
    import json
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(['setup-plan', '--hermes-home', str(tmp_path), '--schedule-dreaming', '--time', '06:15'])
    args.func(args)
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload['config']['memory.provider'] == 'mempalace'
    assert payload['schedule']['time'] == '06:15'
