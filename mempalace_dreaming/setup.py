"""Explicit, testable setup apply layer.

This module turns a dry-run setup plan (see ``build_setup_plan``) into either
a report of what *would* happen (``apply=False``, the default) or actual
side effects (``apply=True``).

All side effects are dependency-injected:

* ``mkdir_fn(path)`` creates a directory;
* ``run_fn(argv)`` runs a config command as an argv list (never a shell
  string).

Scheduling stays report-only here: this layer never creates a real cron job.
There are no Obsidian writes and no memory writes during setup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

MkdirFn = Callable[[str], object]
RunFn = Callable[[Sequence[str]], object]


@dataclass
class SetupResult:
    """Outcome of a setup run (dry-run or applied)."""

    applied: bool = False
    created_directories: list[str] = field(default_factory=list)
    config_commands: list[list[str]] = field(default_factory=list)
    schedule_planned: dict[str, Any] | None = None
    rollback_notes: list[str] = field(default_factory=list)


def _format_value(value: Any) -> str:
    """Render a config value as a CLI argument.

    Booleans become lowercase ``true``/``false`` (Hermes config convention);
    everything else is stringified as-is.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_config_commands(plan: dict[str, Any]) -> list[list[str]]:
    """Turn ``plan["config"]`` into exact ``hermes config set`` argv lists.

    Returns a list of argv lists (never shell strings) so callers can run
    them safely via ``subprocess`` without shell interpolation.
    """
    config = plan.get("config", {})
    return [
        ["hermes", "config", "set", key, _format_value(value)]
        for key, value in config.items()
    ]


def _build_rollback_notes(
    directories: list[str], commands: list[list[str]]
) -> list[str]:
    notes = [
        "Setup makes no destructive changes; rollback is manual and explicit.",
    ]
    if directories:
        notes.append(
            "Remove created directories if unwanted: "
            + ", ".join(directories)
        )
    for argv in commands:
        # argv == ["hermes", "config", "set", <key>, <value>]
        key = argv[3]
        notes.append(
            f"Revert config key '{key}' with: hermes config set {key} <previous-value>"
        )
    notes.append("No cron job is created by setup; nothing to undo for scheduling.")
    return notes


def apply_setup_plan(
    plan: dict[str, Any],
    *,
    mkdir_fn: MkdirFn,
    run_fn: RunFn,
    apply: bool = False,
) -> SetupResult:
    """Apply (or describe) a setup plan.

    With ``apply=False`` (default) neither ``mkdir_fn`` nor ``run_fn`` is
    called; the returned :class:`SetupResult` describes what *would* be done.

    With ``apply=True`` each planned directory is created via ``mkdir_fn``
    and each config command is run via ``run_fn`` as an argv list.

    Scheduling is always report-only: ``schedule_planned`` echoes
    ``plan["schedule"]`` (or ``None``) and no cron side effect is performed.
    """
    directories = list(plan.get("directories", []))
    config_commands = build_config_commands(plan)
    schedule_planned = plan.get("schedule")
    rollback_notes = _build_rollback_notes(directories, config_commands)

    if apply:
        for path in directories:
            mkdir_fn(path)
        for argv in config_commands:
            run_fn(argv)

    return SetupResult(
        applied=apply,
        created_directories=directories,
        config_commands=config_commands,
        schedule_planned=schedule_planned,
        rollback_notes=rollback_notes,
    )
