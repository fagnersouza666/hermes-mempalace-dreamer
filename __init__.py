
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


def _default_mkdir(path: str) -> None:
    Path(path).expanduser().mkdir(parents=True, exist_ok=True)


def _default_run(argv) -> None:
    subprocess.run(list(argv), check=True)


def _apply_setup_from_args(args, *, mkdir_fn=_default_mkdir, run_fn=_default_run):
    """Build the plan from CLI args and apply (or describe) it.

    Factored out of :func:`_handle_cli` so apply mode is unit-testable
    without running Hermes: inject ``mkdir_fn`` / ``run_fn``.
    """
    from mempalace_dreaming.setup import apply_setup_plan

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
