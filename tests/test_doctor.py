"""Doctor command tests (strict TDD — written before implementation).

``doctor`` is a read-only operational audit command. It must:

* print JSON only;
* never mutate config, memory, cron, or the filesystem;
* capture every failure inside the JSON instead of raising;
* expose ok/warnings/recommendations/checks;
* check plugin presence, memory provider, config coherence, and cron state;
* detect duplicate/legacy dreaming jobs;
* optionally compare expected schedule (--expected-time / --timezone).
"""
from pathlib import Path
import argparse
import importlib.util
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "plugin_doctor_test", ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugin_doctor_test"] = module
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


# ---------------------------------------------------------------------------
# Helpers: _is_dreaming_job
# ---------------------------------------------------------------------------


def test_is_dreaming_job_canonical_name():
    module = load_plugin()
    assert module._is_dreaming_job("mempalace-dreaming-daily") is True


def test_is_dreaming_job_legacy_sonhos():
    module = load_plugin()
    assert module._is_dreaming_job("Sonhos diários MemPalace") is True


def test_is_dreaming_job_unrelated():
    module = load_plugin()
    assert module._is_dreaming_job("backup-nightly") is False


def test_is_dreaming_job_contains_dreaming():
    module = load_plugin()
    assert module._is_dreaming_job("my-dreaming-job") is True


def test_is_dreaming_job_contains_sonho():
    module = load_plugin()
    assert module._is_dreaming_job("SonhosDiarios") is True


def test_is_dreaming_job_contains_mempalace_dreaming():
    module = load_plugin()
    assert module._is_dreaming_job("old-mempalace-dreaming") is True


# ---------------------------------------------------------------------------
# Helpers: _parse_cron_jobs
# ---------------------------------------------------------------------------

REALISTIC_CRON_OUTPUT = """\
ID    NAME                          SCHEDULE         STATUS
1     mempalace-dreaming-daily      30 08 * * *      active
2     backup-nightly                0 2 * * *        active
3     Sonhos diários MemPalace      30 05 * * *      active
"""


def test_parse_cron_jobs_realistic_table():
    module = load_plugin()
    jobs = module._parse_cron_jobs(REALISTIC_CRON_OUTPUT)
    assert isinstance(jobs, list)
    names = [j["name"] for j in jobs]
    assert "mempalace-dreaming-daily" in names
    assert "backup-nightly" in names
    # The dreaming job must carry the schedule
    dreaming = next(j for j in jobs if j["name"] == "mempalace-dreaming-daily")
    assert dreaming["schedule"] == "30 08 * * *"


def test_parse_cron_jobs_empty_input():
    module = load_plugin()
    jobs = module._parse_cron_jobs("")
    assert jobs == []


def test_parse_cron_jobs_garbage_input():
    module = load_plugin()
    jobs = module._parse_cron_jobs("not a cron table at all!!!")
    assert isinstance(jobs, list)  # must not raise, may be empty


def test_parse_cron_jobs_key_value_format():
    module = load_plugin()
    text = "name: mempalace-dreaming-daily schedule: 30 08 * * *"
    jobs = module._parse_cron_jobs(text)
    assert any(j["name"] == "mempalace-dreaming-daily" for j in jobs)


def test_parse_cron_jobs_json_array():
    module = load_plugin()
    data = [
        {"name": "mempalace-dreaming-daily", "schedule": "30 08 * * *"},
        {"name": "backup-nightly", "schedule": "0 2 * * *"},
    ]
    jobs = module._parse_cron_jobs(json.dumps(data))
    names = [j["name"] for j in jobs]
    assert "mempalace-dreaming-daily" in names
    assert "backup-nightly" in names


# ---------------------------------------------------------------------------
# Helpers: _parse_cron_blocks
# ---------------------------------------------------------------------------

INDENTED_CRON_BLOCK_OUTPUT = """\
  86ebf7425e3c [active]
    Name:      mempalace-dreaming-daily
    Schedule:  30 08 * * *
    Repeat:    daily
"""


def test_parse_cron_blocks_indented_header():
    """Real ``hermes cron list`` indents the job id under the header line.

    Regression: ``_CRON_BLOCK_HEADER_RE`` must tolerate leading whitespace
    before the job id, otherwise the whole block is skipped and doctor
    wrongly reports the daily job as absent.
    """
    module = load_plugin()
    jobs = module._parse_cron_blocks(INDENTED_CRON_BLOCK_OUTPUT)
    assert isinstance(jobs, list)
    names = [j["name"] for j in jobs]
    assert "mempalace-dreaming-daily" in names
    dreaming = next(j for j in jobs if j["name"] == "mempalace-dreaming-daily")
    assert dreaming["schedule"] == "30 08 * * *"


# ---------------------------------------------------------------------------
# build_doctor_report — success path (all green)
# ---------------------------------------------------------------------------

CONFIG_KEYS = [
    "memory.memory_enabled",
    "memory.user_profile_enabled",
    "memory.provider",
    "plugins.mempalace_dreaming.enabled",
    "plugins.mempalace_dreaming.skill",
]

CRON_LIST_WITH_DAILY = """\
ID    NAME                          SCHEDULE         STATUS
1     mempalace-dreaming-daily      30 08 * * *      active
"""


def _build_all_green_runner(tmp_path, cron_stdout=CRON_LIST_WITH_DAILY, config_overrides=None):
    """All-green runner reflecting the real runtime contract.

    Config is exposed via `hermes config path` -> a real YAML file (there is
    no `hermes config get` subcommand), matching live Hermes behaviour.
    ``config_overrides`` keeps the legacy dotted/string form for existing
    tests; values are coerced to the proper YAML type.
    """
    typed_overrides = None
    if config_overrides:
        typed_overrides = {}
        for dotted, raw in config_overrides.items():
            if dotted.endswith("_enabled") and isinstance(raw, str):
                typed_overrides[dotted] = raw.strip().lower() == "true"
            else:
                typed_overrides[dotted] = raw
    cfg_path = _write_live_config(tmp_path, overrides=typed_overrides)
    mapping = {
        ("hermes", "--version"): _ok("hermes 1.2.3"),
        ("hermes", "memory", "status"): _ok(json.dumps({"provider": "mempalace"})),
        ("hermes", "config", "path"): _ok(str(cfg_path)),
        ("hermes", "cron", "list"): _ok(cron_stdout),
    }
    return _fake_runner(mapping)


def test_doctor_success_path_ok_true(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path)
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)
    assert report["ok"] is True
    assert report["warnings"] == []
    assert report["plugin"] == "mempalace-dreaming"
    assert report["version"] == "1.0.1"
    assert "checks" in report
    assert "recommendations" in report
    # JSON-serializable
    json.dumps(report)


def test_doctor_success_checks_plugin_presence(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path)
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)
    checks = report["checks"]
    assert checks["bundled_skill_exists"] is True
    assert checks["engine_module_available"] is True
    assert checks["setup_module_available"] is True
    assert "plugin_status" in checks


def test_doctor_success_checks_memory(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path)
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)
    checks = report["checks"]
    assert checks["memory_status_ok"] is True
    assert checks["memory_provider"] == "mempalace"
    assert checks["provider_is_mempalace"] is True
    assert checks["hermes_cli_callable"] is True


def test_doctor_success_checks_config_coherent(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path)
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)
    config_checks = report["checks"]["config"]
    assert config_checks["config_coherent"] is True
    # Each key has raw/value/ok/expected
    for key in CONFIG_KEYS:
        entry = config_checks[key]
        assert "raw" in entry
        assert "value" in entry
        assert "ok" in entry
        assert "expected" in entry
        assert entry["ok"] is True


def test_doctor_success_checks_cron(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path)
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)
    cron = report["checks"]["cron"]
    assert cron["daily_job_present"] is True
    assert cron["duplicate_dreaming_jobs"] is False
    assert cron["schedule_mismatch"] is None  # no expected_time given


def test_doctor_hermes_home_expanded(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path)
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)
    assert report["hermes_home"] == str(Path(tmp_path).expanduser())


# ---------------------------------------------------------------------------
# Provider not mempalace
# ---------------------------------------------------------------------------


def test_doctor_provider_not_mempalace(tmp_path):
    module = load_plugin()
    cfg_path = _write_live_config(tmp_path)
    mapping = {
        ("hermes", "--version"): _ok("hermes 1.2.3"),
        ("hermes", "memory", "status"): _ok(json.dumps({"provider": "builtin"})),
        ("hermes", "config", "path"): _ok(str(cfg_path)),
        ("hermes", "cron", "list"): _ok(CRON_LIST_WITH_DAILY),
    }
    run_fn = _fake_runner(mapping)
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)
    assert report["ok"] is False
    assert any("mempalace" in w.lower() for w in report["warnings"])
    assert report["checks"]["provider_is_mempalace"] is False


# ---------------------------------------------------------------------------
# Subprocess failure captured (not raised)
# ---------------------------------------------------------------------------


def test_doctor_subprocess_failure_captured(tmp_path):
    module = load_plugin()
    mapping = {
        ("hermes", "--version"): _fail("command not found"),
        ("hermes", "memory", "status"): _fail("command not found"),
        ("hermes", "config", "path"): _fail("command not found"),
        ("hermes", "cron", "list"): _fail("command not found"),
    }
    run_fn = _fake_runner(mapping)
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)
    assert report["ok"] is False
    assert report["warnings"]
    # Must be JSON-serializable — never raises
    json.dumps(report)


# ---------------------------------------------------------------------------
# run_fn raising an exception — must not propagate
# ---------------------------------------------------------------------------


def test_doctor_run_fn_raises_no_traceback(tmp_path):
    module = load_plugin()

    def exploding_run_fn(argv):
        raise RuntimeError("subprocess exploded unexpectedly")

    # Must not raise
    report = module.build_doctor_report(str(tmp_path), run_fn=exploding_run_fn)
    assert report["ok"] is False
    assert report["warnings"]
    json.dumps(report)


# ---------------------------------------------------------------------------
# Config incoherent
# ---------------------------------------------------------------------------


def test_doctor_config_incoherent_memory_disabled(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path, 
        config_overrides={
            "memory.memory_enabled": "false",
            "memory.provider": "builtin",
        }
    )
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)
    config_checks = report["checks"]["config"]
    assert config_checks["config_coherent"] is False
    assert config_checks["memory.memory_enabled"]["ok"] is False
    assert config_checks["memory.provider"]["ok"] is False
    assert report["ok"] is False
    # Must have warnings about incoherence
    assert report["warnings"]
    # Must have recommendations
    assert report["recommendations"]


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

CRON_LIST_WITH_DUPLICATE = """\
ID    NAME                          SCHEDULE         STATUS
1     mempalace-dreaming-daily      30 08 * * *      active
2     Sonhos diários MemPalace      30 05 * * *      active
"""


def test_doctor_duplicate_dreaming_jobs(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path, cron_stdout=CRON_LIST_WITH_DUPLICATE)
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)
    cron = report["checks"]["cron"]
    assert cron["duplicate_dreaming_jobs"] is True
    assert report["ok"] is False
    assert any("duplicate" in w.lower() or "duplicado" in w.lower() for w in report["warnings"])


# ---------------------------------------------------------------------------
# Daily job absent
# ---------------------------------------------------------------------------

CRON_LIST_NO_DAILY = """\
ID    NAME           SCHEDULE     STATUS
1     backup-nightly 0 2 * * *    active
"""


def test_doctor_daily_job_absent(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path, cron_stdout=CRON_LIST_NO_DAILY)
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)
    cron = report["checks"]["cron"]
    assert cron["daily_job_present"] is False
    assert report["ok"] is False
    assert report["warnings"]


# ---------------------------------------------------------------------------
# Expected-time match
# ---------------------------------------------------------------------------


def test_doctor_expected_time_match(tmp_path):
    """Daily job schedule matches converted UTC cron -> schedule_mismatch False."""
    module = load_plugin()
    # 08:30 UTC = 30 08 * * *
    run_fn = _build_all_green_runner(tmp_path, cron_stdout=CRON_LIST_WITH_DAILY)
    report = module.build_doctor_report(
        str(tmp_path),
        run_fn=run_fn,
        expected_time="08:30",
        timezone="UTC",
    )
    cron = report["checks"]["cron"]
    assert cron["schedule_mismatch"] is False
    assert report["ok"] is True


# ---------------------------------------------------------------------------
# Expected-time mismatch
# ---------------------------------------------------------------------------


def test_doctor_expected_time_mismatch(tmp_path):
    """Daily job schedule differs from expected -> schedule_mismatch True, ok False."""
    module = load_plugin()
    # CRON_LIST_WITH_DAILY has 30 08 * * * but we ask for 06:00 UTC = 00 06 * * *
    run_fn = _build_all_green_runner(tmp_path, cron_stdout=CRON_LIST_WITH_DAILY)
    report = module.build_doctor_report(
        str(tmp_path),
        run_fn=run_fn,
        expected_time="06:00",
        timezone="UTC",
    )
    cron = report["checks"]["cron"]
    assert cron["schedule_mismatch"] is True
    assert report["ok"] is False
    assert report["warnings"]


# ---------------------------------------------------------------------------
# Zero-pad difference treated as match
# ---------------------------------------------------------------------------


CRON_LIST_ZERO_PAD = """\
ID    NAME                          SCHEDULE         STATUS
1     mempalace-dreaming-daily      30 8 * * *       active
"""


def test_doctor_expected_time_zero_pad_match(tmp_path):
    """'30 8 * * *' vs expected '30 08 * * *' must be treated as MATCH."""
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path, cron_stdout=CRON_LIST_ZERO_PAD)
    report = module.build_doctor_report(
        str(tmp_path),
        run_fn=run_fn,
        expected_time="08:30",
        timezone="UTC",
    )
    cron = report["checks"]["cron"]
    assert cron["schedule_mismatch"] is False


# ---------------------------------------------------------------------------
# Invalid timezone with expected-time
# ---------------------------------------------------------------------------


def test_doctor_invalid_timezone_warning_no_traceback(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path)
    # Must not raise
    report = module.build_doctor_report(
        str(tmp_path),
        run_fn=run_fn,
        expected_time="08:30",
        timezone="Bogus/TZ",
    )
    cron = report["checks"]["cron"]
    assert cron.get("expected_cron_utc") is None
    assert cron["schedule_mismatch"] is None
    assert "expected_schedule_error" in cron
    assert report["warnings"]
    json.dumps(report)


# ---------------------------------------------------------------------------
# expected_time omitted -> schedule_mismatch None
# ---------------------------------------------------------------------------


def test_doctor_no_expected_time_schedule_mismatch_is_none(tmp_path):
    """Without expected_time, schedule_mismatch stays None regardless of schedule."""
    module = load_plugin()
    # Use a weird schedule to confirm no false mismatch claim
    weird_cron = "ID    NAME                          SCHEDULE         STATUS\n1     mempalace-dreaming-daily      59 23 * * *      active\n"
    run_fn = _build_all_green_runner(tmp_path, cron_stdout=weird_cron)
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)
    cron = report["checks"]["cron"]
    assert cron["schedule_mismatch"] is None


# ---------------------------------------------------------------------------
# Regression: installed-plugin context
#
# In the installed plugin, `mempalace_dreaming` is not guaranteed to be on
# sys.path, so `from mempalace_dreaming.setup import SCHEDULE_JOB_NAME`
# raises ModuleNotFoundError. doctor must fall back to the plugin-local
# loader (the same strategy already used by setup/apply and lean-check)
# instead of crashing with a traceback.
# ---------------------------------------------------------------------------


def test_doctor_setup_import_fallback_when_package_not_importable(
    tmp_path, monkeypatch
):
    """Package import of mempalace_dreaming.setup fails but the plugin-local
    file is present: build_doctor_report must still return a JSON report.
    """
    module = load_plugin()

    # Simulate the installed-plugin case: the package cannot be imported by
    # name (None in sys.modules makes `import mempalace_dreaming` raise),
    # while mempalace_dreaming/setup.py is still present in PLUGIN_DIR.
    monkeypatch.setitem(sys.modules, "mempalace_dreaming", None)
    monkeypatch.delitem(sys.modules, "mempalace_dreaming.setup", raising=False)
    monkeypatch.delitem(sys.modules, "mempalace_dreaming.engine", raising=False)

    run_fn = _build_all_green_runner(tmp_path)

    # Before the fix this raises:
    #   ModuleNotFoundError: No module named 'mempalace_dreaming'
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)

    assert report["ok"] is True
    assert report["warnings"] == []
    assert report["checks"]["setup_module_available"] is True
    assert report["checks"]["cron"]["daily_job_present"] is True
    json.dumps(report)


def test_doctor_setup_genuinely_unavailable_no_traceback(
    tmp_path, monkeypatch
):
    """If the setup module is genuinely unavailable (no package import and
    no resolvable plugin-local SCHEDULE_JOB_NAME), doctor must degrade into
    a JSON warning instead of raising.
    """
    module = load_plugin()

    monkeypatch.setitem(sys.modules, "mempalace_dreaming", None)
    monkeypatch.delitem(sys.modules, "mempalace_dreaming.setup", raising=False)
    # Force the plugin-local loader to fail too.
    monkeypatch.setattr(module, "_load_schedule_job_name", lambda: None)

    run_fn = _build_all_green_runner(tmp_path)
    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)

    assert report["ok"] is False
    assert any("setup" in w.lower() for w in report["warnings"])
    # No traceback: still a serializable structure.
    assert "checks" in report
    json.dumps(report)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_doctor_cli_prints_valid_json(capsys, tmp_path):
    module = load_plugin()
    args = _parse(module, ["doctor", "--hermes-home", str(tmp_path)])
    args.func(args)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload["ok"], bool)
    assert isinstance(payload["warnings"], list)
    assert "checks" in payload
    assert "recommendations" in payload
    # Read-only: must not create any directory
    assert not (tmp_path / "mempalace").exists()


def test_doctor_cli_default_args():
    module = load_plugin()
    args = _parse(module, ["doctor"])
    assert args.hermes_home == module._default_hermes_home()
    assert args.expected_time is None
    assert args.timezone is None


def test_doctor_cli_graceful_runtime_runner_failure(capsys, tmp_path, monkeypatch):
    """Default run_fn fails -> ok False, valid JSON, no exception.

    Deterministic and environment-independent: instead of relying on
    ``hermes`` being absent from PATH (it may well exist on the runner),
    force every subprocess invocation to fail. The CLI must still print
    valid JSON, report ``ok`` False, and never raise.
    """
    module = load_plugin()

    def _boom(*_a, **_k):
        raise FileNotFoundError("hermes")

    monkeypatch.setattr(module.subprocess, "run", _boom)

    args = _parse(module, ["doctor", "--hermes-home", str(tmp_path)])
    args.func(args)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload["ok"], bool)
    assert payload["ok"] is False  # runtime runner failed
    json.dumps(payload)


# ---------------------------------------------------------------------------
# Regression: live `hermes cron list` block format
#
# The real CLI does NOT emit only a simple table. Each job is a multi-line
# block:
#
#   86ebf7425e3c [active]
#     Name:      mempalace-dreaming-daily
#     Schedule:  30 08 * * *
#     Repeat:    ∞
#
# The previous parser turned this into a bogus job named "Schedule:" and
# missed the real "mempalace-dreaming-daily" job entirely.
# ---------------------------------------------------------------------------

LIVE_CRON_BLOCK = """\
86ebf7425e3c [active]
  Name:      mempalace-dreaming-daily
  Schedule:  30 08 * * *
  Repeat:    ∞
  Next run:  2026-05-17 08:30 UTC

a1b2c3d4e5f6 [active]
  Name:      backup-nightly
  Schedule:  0 2 * * *
  Repeat:    ∞
"""


def test_parse_cron_jobs_live_block_format():
    """Real multi-line block output must be parsed correctly."""
    module = load_plugin()
    jobs = module._parse_cron_jobs(LIVE_CRON_BLOCK)
    names = [j["name"] for j in jobs]
    assert "mempalace-dreaming-daily" in names
    assert "backup-nightly" in names
    # No bogus "Schedule:" job leaked from the value line.
    assert "Schedule:" not in names
    daily = next(j for j in jobs if j["name"] == "mempalace-dreaming-daily")
    assert daily["schedule"] == "30 08 * * *"


# ---------------------------------------------------------------------------
# Regression: config coherence read from the real YAML file
#
# `hermes config` has NO `get` subcommand (only show/edit/set/path/...).
# doctor must resolve the YAML path via `hermes config path` and read it,
# never invoke `hermes config get`.
# ---------------------------------------------------------------------------


def _write_live_config(tmp_path, overrides=None):
    """Write a real-ish Hermes config YAML and return its path.

    The plugins section uses the dash-form plugin id (``mempalace-dreaming``)
    exactly as the live config does, while doctor looks it up via the
    dotted ``plugins.mempalace_dreaming.*`` keys.
    """
    cfg = {
        "memory": {
            "memory_enabled": True,
            "user_profile_enabled": True,
            "provider": "mempalace",
        },
        "plugins": {
            "mempalace-dreaming": {
                "enabled": True,
                "skill": "mempalace-dreaming:mempalace-dreaming",
            }
        },
    }
    if overrides:
        for dotted, value in overrides.items():
            node = cfg
            parts = dotted.split(".")
            # Normalize the plugin id segment to the dash form used on disk.
            parts = [p.replace("mempalace_dreaming", "mempalace-dreaming") for p in parts]
            for seg in parts[:-1]:
                node = node.setdefault(seg, {})
            node[parts[-1]] = value
    import yaml

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def test_doctor_config_loaded_from_yaml_path(tmp_path):
    """doctor reads config via `hermes config path` + YAML, not `config get`."""
    module = load_plugin()
    cfg_path = _write_live_config(tmp_path)
    invoked = []

    def run_fn(argv):
        invoked.append(tuple(argv))
        if tuple(argv) == ("hermes", "--version"):
            return _ok("hermes 1.2.3")
        if tuple(argv) == ("hermes", "memory", "status"):
            return _ok(json.dumps({"provider": "mempalace"}))
        if tuple(argv) == ("hermes", "config", "path"):
            return _ok(str(cfg_path))
        if tuple(argv) == ("hermes", "cron", "list"):
            return _ok(LIVE_CRON_BLOCK)
        raise AssertionError(f"unexpected command: {argv}")

    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)

    # Never call the nonexistent `hermes config get`.
    assert not any(a[:3] == ("hermes", "config", "get") for a in invoked)
    assert ("hermes", "config", "path") in invoked

    config_checks = report["checks"]["config"]
    assert config_checks["config_coherent"] is True
    for key in CONFIG_KEYS:
        assert config_checks[key]["ok"] is True, key


def test_doctor_config_path_failure_warns_no_traceback(tmp_path):
    """If `hermes config path` fails, doctor warns instead of crashing."""
    module = load_plugin()

    def run_fn(argv):
        if tuple(argv) == ("hermes", "--version"):
            return _ok("hermes 1.2.3")
        if tuple(argv) == ("hermes", "memory", "status"):
            return _ok(json.dumps({"provider": "mempalace"}))
        if tuple(argv) == ("hermes", "config", "path"):
            return _fail("config path: not found")
        if tuple(argv) == ("hermes", "cron", "list"):
            return _ok(LIVE_CRON_BLOCK)
        raise AssertionError(f"unexpected command: {argv}")

    report = module.build_doctor_report(str(tmp_path), run_fn=run_fn)
    assert report["ok"] is False
    assert report["checks"]["config"]["config_coherent"] is False
    assert any("config" in w.lower() for w in report["warnings"])
    assert report["recommendations"]
    json.dumps(report)


# ---------------------------------------------------------------------------
# Regression: end-to-end doctor turns green with realistic live outputs
# ---------------------------------------------------------------------------


def test_doctor_live_outputs_end_to_end_green(tmp_path):
    """Realistic live config + cron block output => ok True, no warnings."""
    module = load_plugin()
    cfg_path = _write_live_config(tmp_path)

    def run_fn(argv):
        if tuple(argv) == ("hermes", "--version"):
            return _ok("hermes 1.2.3")
        if tuple(argv) == ("hermes", "memory", "status"):
            return _ok(json.dumps({"provider": "mempalace"}))
        if tuple(argv) == ("hermes", "config", "path"):
            return _ok(str(cfg_path))
        if tuple(argv) == ("hermes", "cron", "list"):
            return _ok(LIVE_CRON_BLOCK)
        raise AssertionError(f"unexpected command: {argv}")

    report = module.build_doctor_report(
        str(tmp_path),
        run_fn=run_fn,
        expected_time="05:30",
        timezone="America/Sao_Paulo",
    )
    assert report["ok"] is True, report["warnings"]
    assert report["warnings"] == []
    assert report["checks"]["cron"]["daily_job_present"] is True
    assert report["checks"]["config"]["config_coherent"] is True
    json.dumps(report)
