"""Explicit, testable setup apply layer.

This module turns a dry-run setup plan (see ``build_setup_plan``) into either
a report of what *would* happen (``apply=False``, the default) or actual
side effects (``apply=True``).

All side effects are dependency-injected:

* ``mkdir_fn(path)`` creates a directory;
* ``run_fn(argv)`` runs a config command as an argv list (never a shell
  string).

Scheduling is only ever performed when *both* ``apply=True`` and
``create_cron=True`` are passed, and only through an injected
``schedule_fn`` (never a hidden global, never a shell string). Post-apply
verification is likewise opt-in (``verify_after_apply=True``) and runs
through an injected, read-only ``verify_fn``. There are no Obsidian writes
and no memory writes during setup or verification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

MkdirFn = Callable[[str], object]
RunFn = Callable[[Sequence[str]], object]
ScheduleFn = Callable[[Sequence[str]], object]
VerifyFn = Callable[[], dict]

#: Deterministic cron job name so re-runs target the same schedule.
SCHEDULE_JOB_NAME = "mempalace-dreaming-daily"

#: Conservative, self-contained prompt for the daily dreaming cron. It is
#: intentionally generic: no secrets, no environment specifics, no chat
#: targeting. It must stay safe to bake verbatim into a cron job.
CONSERVATIVE_DREAM_PROMPT = (
    "Run a conservative MemPalace-first light dreaming pass. "
    "Search MemPalace before remembering anything. Persist only durable, "
    "high-signal, reusable facts; keep writes minimal. Never store secrets, "
    "credentials, task progress, SHAs, issue or PR numbers, or logs. Do not "
    "write to Obsidian. Do not delete or compact memory. If unsure, report "
    "instead of writing."
)


@dataclass
class SetupResult:
    """Outcome of a setup run (dry-run or applied)."""

    applied: bool = False
    created_directories: list[str] = field(default_factory=list)
    config_commands: list[list[str]] = field(default_factory=list)
    schedule_planned: dict[str, Any] | None = None
    cron: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    rollback_notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


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


def _cron_expr_from_time(time: str) -> str:
    """Turn ``"HH:MM"`` into a deterministic daily cron ``"MM HH * * *"``.

    Zero-padded so the spec is byte-stable across runs. Raises ``ValueError``
    on input that is not ``HH:MM`` with integer fields.
    """
    hour_str, _, minute_str = time.partition(":")
    hour = int(hour_str)
    minute = int(minute_str)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time out of range: {time!r}")
    return f"{minute:02d} {hour:02d} * * *"


def build_cron_create_argv(
    schedule: dict[str, Any], *, deliver: str = "local"
) -> list[str]:
    """Build the exact ``hermes cron create`` argv for a schedule spec.

    Matches the real Hermes CLI contract: ``schedule`` and ``prompt`` are
    **positional** arguments, not flags. There is no ``--schedule`` and no
    ``--prompt`` -- inventing them would make every scheduled run fail. The
    shape is::

        hermes cron create --name <name> --deliver <sink> --skill <skill> \
            <cron-expr> <prompt>

    Deterministic and argv-only (never a shell string): the same schedule
    always yields the same argv. The job name is fixed
    (:data:`SCHEDULE_JOB_NAME`) so re-applying targets one schedule rather
    than piling up duplicates. ``deliver`` defaults to the safe ``"local"``
    sink so an accidental schedule never broadcasts to chats. The cron
    expression is the bare positional schedule token; the trailing
    positional is the conservative, self-contained
    :data:`CONSERVATIVE_DREAM_PROMPT`. The bundled skill is always attached.
    """
    # Prefer the timezone-converted UTC cron from the plan; fall back to a
    # direct HH:MM->cron only for legacy callers without a ``cron_utc``
    # (treated as UTC, which is the honest default).
    cron_expr = schedule.get("cron_utc") or _cron_expr_from_time(
        schedule["time"]
    )
    skill = schedule.get("skill", "mempalace-dreaming:mempalace-dreaming")
    return [
        "hermes",
        "cron",
        "create",
        "--name",
        SCHEDULE_JOB_NAME,
        "--deliver",
        deliver,
        "--skill",
        skill,
        # Positional: schedule (cron expression), then optional prompt.
        cron_expr,
        CONSERVATIVE_DREAM_PROMPT,
    ]


def _build_rollback_notes(
    directories: list[str], commands: list[list[str]], create_cron: bool
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
    if create_cron:
        notes.append(
            "A daily dreaming cron may be created with name "
            f"'{SCHEDULE_JOB_NAME}'. To undo: run 'hermes cron list' to find "
            "its job id, then 'hermes cron remove <job_id>' (removal is by "
            "job id, not by name)."
        )
    else:
        notes.append(
            "No cron job is created by setup; nothing to undo for scheduling."
        )
    return notes


def apply_setup_plan(
    plan: dict[str, Any],
    *,
    mkdir_fn: MkdirFn,
    run_fn: RunFn,
    apply: bool = False,
    schedule_fn: ScheduleFn | None = None,
    create_cron: bool = False,
    verify_fn: VerifyFn | None = None,
    verify_after_apply: bool = False,
) -> SetupResult:
    """Apply (or describe) a setup plan.

    With ``apply=False`` (default) no injected function is called at all —
    not ``mkdir_fn``, ``run_fn``, ``schedule_fn``, or ``verify_fn``. The
    returned :class:`SetupResult` only describes what *would* be done;
    ``cron`` and ``verification`` stay ``None`` even if their flags are set.

    With ``apply=True`` each planned directory is created via ``mkdir_fn``
    and each config command is run via ``run_fn`` as an argv list. Failures
    are caught: the first failing action records a useful error string,
    stops all further actions, and the partial :class:`SetupResult` is
    returned instead of raising. If directory creation fails, no config
    command runs; if a config command fails, already-completed actions
    remain recorded and later commands do not run.

    Cron creation happens **only** when ``apply=True`` *and*
    ``create_cron=True``, the plan carries a ``schedule``, and ``schedule_fn``
    is injected. It runs after a clean directory+config apply: if apply
    failed early, no cron is created. The deterministic
    ``hermes cron create`` argv (see :func:`build_cron_create_argv`) is
    passed to ``schedule_fn``; any failure is captured in ``result.cron``
    (``created=False`` + ``error``), never raised. When ``create_cron`` is
    not requested, ``result.cron`` stays ``None`` and scheduling remains
    report-only via ``schedule_planned``.

    Post-apply verification happens **only** when ``apply=True`` *and*
    ``verify_after_apply=True``. It is skipped (recorded as ``ran=False``
    with a reason) if apply failed early, so verification never runs against
    a half-applied environment. Otherwise the injected, read-only
    ``verify_fn`` is called and its report embedded in
    ``result.verification``.
    """
    planned_directories = list(plan.get("directories", []))
    planned_commands = build_config_commands(plan)
    schedule_planned = plan.get("schedule")
    rollback_notes = _build_rollback_notes(
        planned_directories, planned_commands, create_cron
    )

    if not apply:
        return SetupResult(
            applied=False,
            created_directories=planned_directories,
            config_commands=planned_commands,
            schedule_planned=schedule_planned,
            cron=None,
            verification=None,
            rollback_notes=rollback_notes,
            errors=[],
        )

    created_directories: list[str] = []
    config_commands: list[list[str]] = []
    errors: list[str] = []

    for path in planned_directories:
        try:
            mkdir_fn(path)
        except Exception as exc:  # noqa: BLE001 - report, never crash apply
            errors.append(f"Failed to create directory {path!r}: {exc}")
            break
        created_directories.append(path)

    if not errors:
        for argv in planned_commands:
            try:
                run_fn(argv)
            except Exception as exc:  # noqa: BLE001 - report, never crash
                errors.append(f"Failed to run config command {argv!r}: {exc}")
                break
            config_commands.append(argv)

    apply_failed_early = bool(errors)

    cron = _maybe_create_cron(
        schedule_planned,
        schedule_fn=schedule_fn,
        create_cron=create_cron,
        apply_failed_early=apply_failed_early,
    )

    verification = _maybe_verify(
        verify_fn=verify_fn,
        verify_after_apply=verify_after_apply,
        apply_failed_early=apply_failed_early,
    )

    return SetupResult(
        applied=True,
        created_directories=created_directories,
        config_commands=config_commands,
        schedule_planned=schedule_planned,
        cron=cron,
        verification=verification,
        rollback_notes=rollback_notes,
        errors=errors,
    )


def _maybe_create_cron(
    schedule_planned: dict[str, Any] | None,
    *,
    schedule_fn: ScheduleFn | None,
    create_cron: bool,
    apply_failed_early: bool,
) -> dict[str, Any] | None:
    """Create the daily dreaming cron, or explain why it was not.

    Returns ``None`` when cron was not requested (scheduling stays
    report-only). Otherwise returns a JSON-serializable dict with
    ``requested``/``created``/``argv``/``error``. Never raises.
    """
    if not create_cron:
        return None
    if apply_failed_early:
        return {
            "requested": True,
            "created": False,
            "argv": None,
            "error": "skipped: apply failed early; cron not created",
        }
    if not schedule_planned:
        return {
            "requested": True,
            "created": False,
            "argv": None,
            "error": "no schedule in plan; pass --schedule-dreaming",
        }
    if schedule_fn is None:
        return {
            "requested": True,
            "created": False,
            "argv": None,
            "error": "no schedule_fn injected; cannot create cron",
        }
    if not schedule_planned.get("cron_utc"):
        # Timezone conversion failed at plan time (e.g. unknown timezone):
        # never schedule a misleading cron; report the captured reason.
        reason = schedule_planned.get(
            "timezone_error", "no UTC cron in schedule; not creating cron"
        )
        return {
            "requested": True,
            "created": False,
            "argv": None,
            "error": f"cron not created: {reason}",
        }
    argv = build_cron_create_argv(schedule_planned)
    try:
        schedule_fn(argv)
    except Exception as exc:  # noqa: BLE001 - report, never crash apply
        return {
            "requested": True,
            "created": False,
            "argv": argv,
            "error": f"cron creation failed: {exc}",
        }
    return {"requested": True, "created": True, "argv": argv, "error": ""}


def _maybe_verify(
    *,
    verify_fn: VerifyFn | None,
    verify_after_apply: bool,
    apply_failed_early: bool,
) -> dict[str, Any] | None:
    """Run read-only post-apply verification, or explain why it was skipped.

    Returns ``None`` when verification was not requested. When requested but
    apply failed early, returns ``{"ran": False, "reason": ...}`` without
    calling ``verify_fn`` (never verify a half-applied environment). On a
    clean apply the injected read-only ``verify_fn`` is called and its
    report embedded. Never raises.
    """
    if not verify_after_apply:
        return None
    if apply_failed_early:
        return {
            "ran": False,
            "reason": "skipped: apply failed early; not verifying",
        }
    if verify_fn is None:
        return {
            "ran": False,
            "reason": "no verify_fn injected; cannot verify",
        }
    try:
        report = verify_fn()
    except Exception as exc:  # noqa: BLE001 - report, never crash apply
        return {
            "ran": False,
            "reason": f"verification raised: {exc}",
        }
    return {"ran": True, "report": report}
