"""Runtime verification tests (strict TDD, written before implementation).

``verify-runtime`` is the live, read-only environment check. It must:

* print JSON only;
* never mutate config, memory, cron, or the filesystem;
* capture subprocess failures inside the JSON instead of raising;
* expose a top-level ``ok`` boolean and a ``warnings`` list;
* detect whether the active memory provider looks like ``mempalace``.
"""
from pathlib import Path
import argparse
import importlib.util
import json
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "plugin_verify_test", ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugin_verify_test"] = module
    spec.loader.exec_module(module)
    return module


def _parse(module, argv):
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    return parser.parse_args(argv)


def _fake_runner(mapping):
    """Return a run_fn that maps a command tuple to a canned result dict."""

    def run_fn(argv):
        return mapping[tuple(argv)]

    return run_fn


def _ok(stdout=""):
    return {
        "ok": True,
        "returncode": 0,
        "stdout": stdout,
        "stderr": "",
        "error": "",
    }


def _fail(error="boom"):
    return {
        "ok": False,
        "returncode": 1,
        "stdout": "",
        "stderr": error,
        "error": error,
    }


# --- pure helper: build_runtime_verification ------------------------------


def test_runtime_verification_success(tmp_path):
    module = load_plugin()
    # The expected mempalace directories must exist for an all-green run.
    for sub in ("palace", "hermes-corpus"):
        (tmp_path / "mempalace" / sub).mkdir(parents=True)

    run_fn = _fake_runner(
        {
            ("hermes", "--version"): _ok("hermes 1.2.3"),
            ("hermes", "memory", "status"): _ok(
                json.dumps({"provider": "mempalace"})
            ),
        }
    )

    result = module.build_runtime_verification(
        hermes_home=str(tmp_path), run_fn=run_fn
    )

    assert result["ok"] is True
    assert result["warnings"] == []
    checks = result["checks"]
    assert checks["hermes_cli_callable"] is True
    assert checks["memory_status_ok"] is True
    assert checks["memory_provider"] == "mempalace"
    assert checks["provider_is_mempalace"] is True
    assert checks["bundled_skill_exists"] is True
    assert checks["engine_module_available"] is True
    assert checks["setup_module_available"] is True
    assert checks["all_directories_exist"] is True


def test_runtime_verification_provider_not_mempalace(tmp_path):
    module = load_plugin()
    for sub in ("palace", "hermes-corpus"):
        (tmp_path / "mempalace" / sub).mkdir(parents=True)

    run_fn = _fake_runner(
        {
            ("hermes", "--version"): _ok("hermes 1.2.3"),
            ("hermes", "memory", "status"): _ok(
                json.dumps({"provider": "builtin"})
            ),
        }
    )

    result = module.build_runtime_verification(
        hermes_home=str(tmp_path), run_fn=run_fn
    )

    assert result["checks"]["memory_provider"] == "builtin"
    assert result["checks"]["provider_is_mempalace"] is False
    assert result["ok"] is False
    assert any("mempalace" in w.lower() for w in result["warnings"])


def test_runtime_verification_captures_subprocess_failure(tmp_path):
    module = load_plugin()
    run_fn = _fake_runner(
        {
            ("hermes", "--version"): _fail("command not found: hermes"),
            ("hermes", "memory", "status"): _fail("command not found: hermes"),
        }
    )

    result = module.build_runtime_verification(
        hermes_home=str(tmp_path), run_fn=run_fn
    )

    assert result["ok"] is False
    assert result["checks"]["hermes_cli_callable"] is False
    assert result["checks"]["memory_status_ok"] is False
    assert result["checks"]["memory_provider"] is None
    assert result["warnings"]
    # The whole structure must be JSON serializable.
    json.dumps(result)


def test_runtime_verification_does_not_raise_if_runner_raises(tmp_path):
    module = load_plugin()

    def exploding_run_fn(argv):
        raise RuntimeError("subprocess exploded")

    result = module.build_runtime_verification(
        hermes_home=str(tmp_path), run_fn=exploding_run_fn
    )

    assert result["ok"] is False
    assert result["checks"]["hermes_cli_callable"] is False
    assert any("exploded" in w for w in result["warnings"])


def test_default_verify_runner_handles_missing_binary():
    module = load_plugin()
    res = module._default_verify_run(
        ["hermes-mempalace-definitely-not-a-real-binary-xyz"]
    )
    assert res["ok"] is False
    assert res["error"]


def test_detect_memory_provider_variants():
    module = load_plugin()
    assert (
        module._detect_memory_provider(json.dumps({"provider": "mempalace"}))
        == "mempalace"
    )
    assert (
        module._detect_memory_provider(
            json.dumps({"memory": {"provider": "MemPalace"}})
        )
        == "mempalace"
    )
    assert (
        module._detect_memory_provider("Provider: builtin\nstatus: ok")
        == "builtin"
    )
    assert module._detect_memory_provider("") is None
    assert module._detect_memory_provider("no provider field here") is None


# --- CLI: verify-runtime --------------------------------------------------


def test_verify_runtime_cli_prints_json_and_mutates_nothing(capsys, tmp_path):
    module = load_plugin()
    args = _parse(module, ["verify-runtime", "--hermes-home", str(tmp_path)])
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload["ok"], bool)
    assert isinstance(payload["warnings"], list)
    assert "checks" in payload
    assert payload["hermes_home"] == str(Path(tmp_path).expanduser())
    # Read-only: verify-runtime must not create the mempalace directories.
    assert not (tmp_path / "mempalace").exists()


def test_verify_runtime_defaults_hermes_home():
    module = load_plugin()
    args = _parse(module, ["verify-runtime"])
    assert args.hermes_home == module._default_hermes_home()
    assert hasattr(args, "func")