"""Detection of the unsupported built-in short-memory cleanup cron (#9763).

Upstream defect (NousResearch/hermes-agent#9763): the Hermes cron scheduler
creates agents with ``skip_memory=True``, so neither the built-in memory
store nor the memory tool is available in cron sessions. A cron job whose
prompt asks the agent to clean the built-in short memory (MEMORY.md /
USER.md via ``memory(action=...)``) therefore cannot work: the job reports
"ok" while the cleanup silently does nothing.

The plugin must not patch Hermes core and must not pretend the cleanup
succeeded. Instead, ``doctor`` reads ``$HERMES_HOME/cron/jobs.json``
(read-only), detects such jobs, and reports the upstream limitation
explicitly; ``repair-plan`` proposes a manual, report-only remediation;
the setup plan documents the limitation.
"""
from pathlib import Path
import importlib.util
import json
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "plugin_short_memory_cron_test", ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fake_runner(mapping):
    def run_fn(argv):
        return mapping[tuple(argv)]

    return run_fn


def _ok(stdout=""):
    return {"ok": True, "returncode": 0, "stdout": stdout, "stderr": "",
            "error": ""}


CLEANUP_JOB = {
    "id": "03df4f0014ad",
    "name": "Limpeza diária da memória curta",
    "prompt": (
        "Execute uma rotina diária conservadora de limpeza da memória curta "
        "do Hermes. Objetivo principal: limpar a memória curta built-in "
        "exposta no prompt (MEMORY/USER PROFILE), usando a ferramenta memory "
        "quando houver ação segura. Aplique memory(action='remove') ou "
        "memory(action='replace') somente para alterações obviamente seguras."
    ),
    "no_agent": False,
    "enabled": True,
}

SCRIPT_JOB = {
    "id": "d91cd5607493",
    "name": "Doctor diário do Hermes e atualização do MemPalace",
    "prompt": "unused - script-only maintenance",
    "script": "hermes_doctor_mempalace.sh",
    "no_agent": True,
    "enabled": True,
}

BENIGN_JOB = {
    "id": "86ebf7425e3c",
    "name": "mempalace-dreaming-daily",
    "prompt": "Execute uma rotina diária conservadora de MemPalace Dreaming.",
    "no_agent": False,
    "enabled": True,
}

# Real-shaped active dreaming job (regression for the v1.1.1 doctor false
# positive): the prompt forbids touching MEMORY.md/USER.md ("Não altere ...")
# and uses "compacto" as an adjective on another line. Neither may count as
# scheduled built-in short-memory cleanup.
DREAMING_JOB_REAL = {
    "id": "86ebf7425e3c",
    "name": "mempalace-dreaming-daily",
    "prompt": (
        "Execute uma rotina diária conservadora de MemPalace Dreaming, "
        "com foco em reduzir ruído.\n"
        "Regras obrigatórias:\n"
        "4. Para cada candidato, use mempalace_search antes de "
        "mempalace_remember.\n"
        "5. Use mempalace_remember apenas para fato novo, compacto, "
        "declarativo e não duplicado.\n"
        "6. Não use built-in memory() para fatos comuns; MemPalace é a "
        "memória semântica principal.\n"
        "7. Não altere MEMORY.md/USER.md, config, skills, cron jobs ou "
        "Obsidian durante esta rotina.\n"
    ),
    "no_agent": False,
    "enabled": True,
    "state": "scheduled",
}

# Real-shaped paused built-in cleanup job: paused jobs cannot fire, so they
# are not an active #9763 problem and must not fail doctor.
CLEANUP_JOB_PAUSED = dict(
    CLEANUP_JOB, enabled=False, state="paused",
    paused_at="2026-07-12T10:50:35-03:00",
)


def _write_jobs(home: Path, jobs):
    cron_dir = home / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    (cron_dir / "jobs.json").write_text(
        json.dumps(jobs, ensure_ascii=False), encoding="utf-8"
    )


def _runner(tmp_path):
    mapping = {
        ("hermes", "--version"): _ok("hermes 1.2.3"),
        ("hermes", "memory", "status"): _ok(json.dumps({"provider": "mempalace"})),
        ("hermes", "config", "path"): _ok(str(tmp_path / "nonexistent.yaml")),
        ("hermes", "cron", "list"): _ok(""),
    }
    return _fake_runner(mapping)


# ---------------------------------------------------------------------------
# Detection helper
# ---------------------------------------------------------------------------


def test_detects_short_memory_cleanup_job():
    module = load_plugin()
    found = module._detect_short_memory_cleanup_jobs(
        [CLEANUP_JOB, SCRIPT_JOB, BENIGN_JOB]
    )
    assert [j["id"] for j in found] == ["03df4f0014ad"]
    assert found[0]["name"] == "Limpeza diária da memória curta"


def test_script_only_and_benign_jobs_are_not_flagged():
    module = load_plugin()
    assert module._detect_short_memory_cleanup_jobs([SCRIPT_JOB, BENIGN_JOB]) == []


def test_english_short_memory_cleanup_is_flagged():
    module = load_plugin()
    job = {
        "id": "ff00",
        "name": "daily short memory cleanup",
        "prompt": (
            "Clean up the built-in short-term memory (MEMORY.md) removing "
            "obsolete entries with memory(action='remove')."
        ),
        "no_agent": False,
    }
    assert [j["id"] for j in module._detect_short_memory_cleanup_jobs([job])] == ["ff00"]


def test_disabled_job_is_not_flagged():
    module = load_plugin()
    job = dict(CLEANUP_JOB, enabled=False)
    assert module._detect_short_memory_cleanup_jobs([job]) == []


def test_paused_state_job_is_not_flagged_even_without_enabled_key():
    module = load_plugin()
    job = dict(CLEANUP_JOB, state="paused")
    job.pop("enabled")
    assert module._detect_short_memory_cleanup_jobs([job]) == []


def test_real_dreaming_prompt_is_not_flagged():
    """Regression (v1.1.1): the dreaming prompt mentions MEMORY.md/USER.md
    only in a prohibition and 'compacto' as an adjective elsewhere; the
    detector must not combine them into a false cleanup flag."""
    module = load_plugin()
    assert module._detect_short_memory_cleanup_jobs([DREAMING_JOB_REAL]) == []


def test_active_cleanup_is_flagged_next_to_paused_and_dreaming_jobs():
    module = load_plugin()
    found = module._detect_short_memory_cleanup_jobs(
        [DREAMING_JOB_REAL, CLEANUP_JOB_PAUSED, dict(CLEANUP_JOB, id="aa11")]
    )
    assert [j["id"] for j in found] == ["aa11"]


# ---------------------------------------------------------------------------
# Doctor integration
# ---------------------------------------------------------------------------


def test_doctor_reports_9763_for_cleanup_cron(tmp_path):
    module = load_plugin()
    _write_jobs(tmp_path, [CLEANUP_JOB, BENIGN_JOB])
    report = module.build_doctor_report(str(tmp_path), run_fn=_runner(tmp_path))

    cron = report["checks"]["cron"]
    flagged = cron["short_memory_cleanup_jobs"]
    assert [j["id"] for j in flagged] == ["03df4f0014ad"]
    assert cron["short_memory_cleanup_unsupported"] is True
    assert report["ok"] is False

    joined = " ".join(report["warnings"])
    assert "#9763" in joined
    assert "skip_memory" in joined
    rec = " ".join(report["recommendations"])
    assert "interactive" in rec or "interativ" in rec


def test_doctor_without_jobs_file_stays_quiet(tmp_path):
    module = load_plugin()
    report = module.build_doctor_report(str(tmp_path), run_fn=_runner(tmp_path))
    cron = report["checks"]["cron"]
    assert cron["short_memory_cleanup_jobs"] == []
    assert cron["short_memory_cleanup_unsupported"] is False
    assert not any("#9763" in w for w in report["warnings"])


def test_doctor_with_benign_jobs_stays_quiet(tmp_path):
    module = load_plugin()
    _write_jobs(tmp_path, [BENIGN_JOB, SCRIPT_JOB])
    report = module.build_doctor_report(str(tmp_path), run_fn=_runner(tmp_path))
    assert report["checks"]["cron"]["short_memory_cleanup_jobs"] == []
    assert not any("#9763" in w for w in report["warnings"])


def test_doctor_with_real_shaped_jobs_file_stays_quiet(tmp_path):
    """Regression (v1.1.1): with the real jobs.json shape ({"jobs": [...]})
    holding the active dreaming job and the paused built-in cleanup job,
    doctor must not raise the #9763 flag."""
    module = load_plugin()
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir(parents=True)
    (cron_dir / "jobs.json").write_text(
        json.dumps(
            {"jobs": [DREAMING_JOB_REAL, CLEANUP_JOB_PAUSED], "output": {}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = module.build_doctor_report(str(tmp_path), run_fn=_runner(tmp_path))
    cron = report["checks"]["cron"]
    assert cron["short_memory_cleanup_jobs"] == []
    assert cron["short_memory_cleanup_unsupported"] is False
    assert not any("#9763" in w for w in report["warnings"])


def test_doctor_with_malformed_jobs_file_degrades(tmp_path):
    module = load_plugin()
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir(parents=True)
    (cron_dir / "jobs.json").write_text("{not json", encoding="utf-8")
    report = module.build_doctor_report(str(tmp_path), run_fn=_runner(tmp_path))
    cron = report["checks"]["cron"]
    assert cron["short_memory_cleanup_jobs"] == []
    assert any("jobs.json" in w for w in report["warnings"])
    json.dumps(report)


# ---------------------------------------------------------------------------
# Repair plan integration
# ---------------------------------------------------------------------------


def test_repair_plan_proposes_manual_remediation(tmp_path):
    module = load_plugin()
    _write_jobs(tmp_path, [CLEANUP_JOB])
    plan = module.build_repair_plan(str(tmp_path), run_fn=_runner(tmp_path))
    repair = next(
        (r for r in plan["repairs"] if r["id"] == "unsupported-short-memory-cleanup-cron"),
        None,
    )
    assert repair is not None
    assert "9763" in repair["reason"]
    assert repair["command_preview"] == "hermes cron list"
    # Report-only: no destructive command is suggested for auto-run.
    assert "remove" not in (repair["command_preview"] or "")


# ---------------------------------------------------------------------------
# Setup plan documentation
# ---------------------------------------------------------------------------


def test_setup_plan_documents_9763_limitation(tmp_path):
    module = load_plugin()
    plan = module.build_setup_plan(hermes_home=str(tmp_path))
    joined = " ".join(plan["notes"])
    assert "#9763" in joined
    assert "skip_memory" in joined
