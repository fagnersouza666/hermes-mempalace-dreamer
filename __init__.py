
"""Hermes MemPalace Dreaming plugin.

Bootstrap plugin that ships a MemPalace-first dreaming skill and a small CLI
surface for safe setup planning. It intentionally does not mutate config at
import/register time.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parent
SKILL_PATH = PLUGIN_DIR / "skills" / "mempalace-dreaming" / "SKILL.md"
PLUGIN_NAME = "mempalace-dreaming"
PLUGIN_VERSION = "0.1.0"
PLUGIN_STATUS = "public MVP v0.1"


def build_schedule_plan(time: str = "05:30") -> dict[str, Any]:
    """Return a report-only daily dreaming schedule plan.

    This describes *what* a conservative daily dreaming cron would look like.
    It is purely informational: nothing here creates a cron job. Schedule it
    yourself with your Hermes cron tooling if you want automation.
    """
    return {
        "name": "MemPalace Dreaming",
        "time": time,
        "prompt_profile": "daily-conservative",
        "skill": "plugin:mempalace-dreaming",
        "report_only": True,
        "note": (
            "Report-only: no cron job is created. Schedule this manually "
            "via your Hermes cron tooling if you want daily dreaming."
        ),
    }


def _module_importable(module_name: str) -> bool:
    """True if ``module_name`` resolves from PLUGIN_DIR, without importing it.

    Uses ``importlib.util.find_spec`` against a path that always works for a
    GitHub-cloned plugin, so the check is independent of ``sys.path`` and has
    no import side effects.
    """
    import importlib.util

    relative = module_name.replace(".", "/") + ".py"
    return (PLUGIN_DIR / relative).is_file() and (
        importlib.util.spec_from_file_location(
            module_name, PLUGIN_DIR / relative
        )
        is not None
    )


def _build_status() -> dict[str, Any]:
    """Describe plugin/version/status and safety flags. Pure.

    Never calls Hermes memory and never mutates anything; the result depends
    only on what files are present on disk.
    """
    return {
        "plugin": PLUGIN_NAME,
        "version": PLUGIN_VERSION,
        "status": PLUGIN_STATUS,
        "bundled_skill_exists": SKILL_PATH.is_file(),
        "engine_module_available": _module_importable(
            "mempalace_dreaming.engine"
        ),
        "setup_module_available": _module_importable(
            "mempalace_dreaming.setup"
        ),
        "safety": {
            "no_obsidian_writes": True,
            "no_setup_memory_writes": True,
            "schedule_report_only": True,
        },
    }


def build_setup_plan(hermes_home: str | Path, schedule_dreaming: bool = False, time: str = "05:30") -> dict[str, Any]:
    """Return an idempotent setup plan without applying it.

    The real installer can consume this structure to print a diff, create
    directories, set Hermes config keys, and optionally create a cron job.
    Keeping this pure makes it testable and safe.
    """
    home = Path(hermes_home).expanduser()
    plan: dict[str, Any] = {
        "directories": [
            str(home / "mempalace" / "palace"),
            str(home / "mempalace" / "hermes-corpus"),
        ],
        "config": {
            "memory.memory_enabled": True,
            "memory.user_profile_enabled": True,
            "memory.provider": "mempalace",
            "plugins.mempalace_dreaming.enabled": True,
            "plugins.mempalace_dreaming.skill": "plugin:mempalace-dreaming",
        },
        "skill": {
            "name": "mempalace-dreaming",
            "qualified_name": "mempalace-dreaming:mempalace-dreaming",
            "path": str(SKILL_PATH),
        },
        "notes": [
            "Install/enable a MemPalace MemoryProvider separately or via a future installer step.",
            "Restart Hermes or start a fresh session after config changes.",
            "Dreaming cron is opt-in; daily automation must be conservative and report-first.",
        ],
    }
    if schedule_dreaming:
        plan["schedule"] = {
            "name": "MemPalace Dreaming",
            "time": time,
            "prompt_profile": "daily-conservative",
            "skill": "plugin:mempalace-dreaming",
        }
    return plan


def _setup_cli_parser(parser) -> None:
    sub = parser.add_subparsers(dest="mempalace_dreaming_command")
    plan = sub.add_parser("setup-plan", help="Print a safe MemPalace Dreaming setup plan")
    plan.add_argument("--hermes-home", default="~/.hermes", help="Hermes home directory")
    plan.add_argument("--schedule-dreaming", action="store_true", help="Include optional dreaming cron plan")
    plan.add_argument("--time", default="05:30", help="Local time for optional dreaming cron")
    plan.set_defaults(func=_handle_cli)

    setup = sub.add_parser(
        "setup",
        help="Dry-run (default) or --apply the MemPalace Dreaming setup",
    )
    setup.add_argument("--hermes-home", default="~/.hermes", help="Hermes home directory")
    setup.add_argument("--schedule-dreaming", action="store_true", help="Include optional dreaming cron plan (report-only)")
    setup.add_argument("--time", default="05:30", help="Local time for optional dreaming cron")
    setup.add_argument(
        "--apply",
        action="store_true",
        help="Create directories and run 'hermes config set ...' (default is dry-run)",
    )
    setup.set_defaults(func=_handle_cli)

    status = sub.add_parser(
        "status",
        help="Print plugin status and safety flags as JSON (read-only)",
    )
    status.set_defaults(func=_handle_cli)

    schedule_plan = sub.add_parser(
        "schedule-plan",
        help="Print a report-only daily dreaming schedule plan (no cron)",
    )
    schedule_plan.add_argument(
        "--time", default="05:30", help="Local time for the planned dreaming run"
    )
    schedule_plan.set_defaults(func=_handle_cli)


def _default_mkdir(path: str) -> None:
    Path(path).expanduser().mkdir(parents=True, exist_ok=True)


def _default_run(argv) -> None:
    subprocess.run(list(argv), check=True)


def _load_apply_setup_plan():
    """Resolve ``apply_setup_plan`` without depending on ``sys.path``.

    Tries the normal package import first; if the repo root is not on
    ``sys.path`` (e.g. the plugin file is loaded standalone via
    ``importlib.util.spec_from_file_location``), it falls back to loading
    ``mempalace_dreaming/setup.py`` directly from :data:`PLUGIN_DIR`.
    """
    try:
        from mempalace_dreaming.setup import apply_setup_plan

        return apply_setup_plan
    except ImportError:
        import importlib.util
        import sys

        setup_path = PLUGIN_DIR / "mempalace_dreaming" / "setup.py"
        spec = importlib.util.spec_from_file_location(
            "mempalace_dreaming_setup", setup_path
        )
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise
        module = importlib.util.module_from_spec(spec)
        # Register before exec so dataclasses can resolve annotations
        # (``sys.modules[cls.__module__]`` must exist).
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module.apply_setup_plan


def _apply_setup_from_args(args, *, mkdir_fn=_default_mkdir, run_fn=_default_run):
    """Build the plan from CLI args and apply (or describe) it.

    Factored out of :func:`_handle_cli` so apply mode is unit-testable
    without running Hermes: inject ``mkdir_fn`` / ``run_fn``.
    """
    apply_setup_plan = _load_apply_setup_plan()

    plan = build_setup_plan(
        hermes_home=getattr(args, "hermes_home", "~/.hermes"),
        schedule_dreaming=getattr(args, "schedule_dreaming", False),
        time=getattr(args, "time", "05:30"),
    )
    return apply_setup_plan(
        plan,
        mkdir_fn=mkdir_fn,
        run_fn=run_fn,
        apply=getattr(args, "apply", False),
    )


def _handle_cli(args) -> None:
    cmd = getattr(args, "mempalace_dreaming_command", None)
    if cmd == "status":
        print(json.dumps(_build_status(), indent=2, ensure_ascii=False))
        return
    if cmd == "schedule-plan":
        plan = build_schedule_plan(time=getattr(args, "time", "05:30"))
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return
    if cmd == "setup":
        result = _apply_setup_from_args(args)
        print(json.dumps(dataclasses.asdict(result), indent=2, ensure_ascii=False))
        return
    if cmd in (None, "setup-plan"):
        plan = build_setup_plan(
            hermes_home=getattr(args, "hermes_home", "~/.hermes"),
            schedule_dreaming=getattr(args, "schedule_dreaming", False),
            time=getattr(args, "time", "05:30"),
        )
        print(json.dumps(plan, indent=2, ensure_ascii=False))


def register(ctx) -> None:
    """Register plugin-provided skill and CLI command."""
    ctx.register_skill(
        "mempalace-dreaming",
        SKILL_PATH,
        "MemPalace-first dreaming, memory consolidation, and lean-check policy.",
    )
    ctx.register_cli_command(
        name="mempalace-dreaming",
        help="MemPalace-first dreaming setup/status helpers",
        setup_fn=_setup_cli_parser,
        handler_fn=_handle_cli,
        description="Print safe setup plans and helpers for MemPalace-first memory dreaming.",
    )
