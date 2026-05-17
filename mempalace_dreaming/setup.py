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
ProviderCopyFn = Callable[[str, str], object]
ProviderInstallFn = Callable[[Sequence[str]], object]

#: Deterministic cron job name so re-runs target the same schedule.
SCHEDULE_JOB_NAME = "mempalace-dreaming-daily"

#: Deterministic name for the weekly, live-provider lean-check cron. Distinct
#: from the daily dreaming job so the two never collide on re-apply.
LEAN_CHECK_JOB_NAME = "mempalace-dreaming-weekly-lean-check"

#: Conservative prompt for the weekly lean-check cron. Unlike the daily
#: dreaming pass it explicitly queries the *live* MemPalace backend, but it
#: stays strictly report-only: it must never delete, compact, rewrite, or
#: persist memory, and never touch cron/config/Obsidian. Safe to bake
#: verbatim into a cron job: no secrets, no environment specifics.
CONSERVATIVE_LEAN_CHECK_PROMPT = (
    "Run a weekly MemPalace lean-check. Query the live MemPalace backend "
    "read-only (mempalace_status / mempalace_search) and audit memory "
    "quality: duplicate or near-duplicate clusters, stale temporary state, "
    "overly broad memories, contradictions, and supersede candidates. "
    "Return a short report only. Do NOT delete, compact, rewrite, or persist "
    "any memory. Do not write to Obsidian. Do not change cron or config. "
    "Any cleanup must be proposed for explicit human approval, never applied."
)

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
    lean_check_schedule_planned: dict[str, Any] | None = None
    lean_check_cron: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    provider_install_planned: dict[str, Any] | None = None
    provider: dict[str, Any] | None = None
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


def build_lean_check_cron_argv(
    schedule: dict[str, Any], *, deliver: str = "local"
) -> list[str]:
    """Build the exact ``hermes cron create`` argv for the weekly lean-check.

    Same real-CLI contract as :func:`build_cron_create_argv` (positional
    ``schedule`` + ``prompt``, no invented flags), but with the fixed weekly
    job name :data:`LEAN_CHECK_JOB_NAME` and the report-only
    :data:`CONSERVATIVE_LEAN_CHECK_PROMPT`. The cron expression is the
    weekly ``cron_utc`` (``"MM HH * * D"``) computed at plan time; there is
    no HH:MM fallback because a weekly schedule needs the day-of-week field.
    Deterministic and argv-only (never a shell string). ``deliver`` defaults
    to the safe ``"local"`` sink.
    """
    cron_expr = schedule["cron_utc"]
    skill = schedule.get("skill", "mempalace-dreaming:mempalace-dreaming")
    return [
        "hermes",
        "cron",
        "create",
        "--name",
        LEAN_CHECK_JOB_NAME,
        "--deliver",
        deliver,
        "--skill",
        skill,
        cron_expr,
        CONSERVATIVE_LEAN_CHECK_PROMPT,
    ]


#: Honest uninstall hint per install method (used in rollback notes).
_PROVIDER_UNINSTALL_HINTS = {
    "uv": "uv tool uninstall mempalace",
    "pipx": "pipx uninstall mempalace",
    "pip-user": "pip uninstall mempalace",
}


def _build_rollback_notes(
    directories: list[str],
    commands: list[list[str]],
    create_cron: bool,
    install_provider: bool = False,
    create_lean_check_cron: bool = False,
    install_method: str = "auto",
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
    if create_lean_check_cron:
        notes.append(
            "A weekly lean-check cron may be created with name "
            f"'{LEAN_CHECK_JOB_NAME}'. To undo: run 'hermes cron list' to "
            "find its job id, then 'hermes cron remove <job_id>' (removal is "
            "by job id, not by name)."
        )
    else:
        notes.append(
            "No weekly lean-check cron is created by setup; nothing to undo "
            "for the lean-check schedule."
        )
    if install_provider:
        if install_method == "auto":
            undo_hint = (
                "uninstall with whichever tool succeeded: "
                "'uv tool uninstall mempalace' (uv), "
                "'pipx uninstall mempalace' (pipx), or "
                "'pip uninstall mempalace' (pip-user)"
            )
        else:
            undo_hint = (
                "uninstall with: "
                f"'{_PROVIDER_UNINSTALL_HINTS.get(install_method, 'uv tool uninstall mempalace')}'"
            )
        notes.append(
            "Provider bootstrap copies files into "
            "$HERMES_HOME/plugins/mempalace/ and installs the 'mempalace' "
            f"CLI (install method: {install_method}). To undo: remove that "
            f"plugin directory and {undo_hint}."
        )
    else:
        notes.append(
            "No MemPalace provider is installed by setup; nothing to undo "
            "for the provider."
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
    lean_check_schedule_fn: ScheduleFn | None = None,
    create_lean_check_cron: bool = False,
    verify_fn: VerifyFn | None = None,
    verify_after_apply: bool = False,
    provider_copy_fn: ProviderCopyFn | None = None,
    provider_install_fn: ProviderInstallFn | None = None,
    install_provider: bool = False,
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

    The weekly lean-check cron is created **only** when ``apply=True`` *and*
    ``create_lean_check_cron=True``, the plan carries a
    ``lean_check_schedule``, and ``lean_check_schedule_fn`` is injected. It
    uses the same early-failure gating as the daily cron and a separate
    deterministic job name; its outcome is captured in
    ``result.lean_check_cron`` (never raised). Otherwise it stays
    report-only via ``lean_check_schedule_planned``.

    Post-apply verification happens **only** when ``apply=True`` *and*
    ``verify_after_apply=True``. It is skipped (recorded as ``ran=False``
    with a reason) if apply failed early, so verification never runs against
    a half-applied environment. Otherwise the injected, read-only
    ``verify_fn`` is called and its report embedded in
    ``result.verification``.

    MemPalace provider bootstrap happens **only** when ``apply=True`` *and*
    ``install_provider=True``, after a clean directory+config apply. The
    injected ``provider_copy_fn(source, target)`` copies each bundled
    provider artifact into ``$HERMES_HOME/plugins/mempalace/`` and the
    injected ``provider_install_fn(argv)`` runs the ``mempalace`` CLI
    install as an argv list (never a shell string). The outcome is captured
    in ``result.provider`` (``ok``/``copied_files``/``cli_install``/
    ``error``); any failure is reported there, never raised, and is treated
    as an early failure so cron/verify are skipped. When ``install_provider``
    is not requested, ``result.provider`` stays ``None`` and the bundle is
    only described via ``provider_install_planned``.
    """
    planned_directories = list(plan.get("directories", []))
    planned_commands = build_config_commands(plan)
    schedule_planned = plan.get("schedule")
    lean_check_schedule_planned = plan.get("lean_check_schedule")
    provider_install_planned = plan.get("provider_install")
    provider_install_method = (provider_install_planned or {}).get(
        "install_method", "auto"
    )
    rollback_notes = _build_rollback_notes(
        planned_directories,
        planned_commands,
        create_cron,
        install_provider,
        create_lean_check_cron,
        provider_install_method,
    )

    if not apply:
        return SetupResult(
            applied=False,
            created_directories=planned_directories,
            config_commands=planned_commands,
            schedule_planned=schedule_planned,
            cron=None,
            lean_check_schedule_planned=lean_check_schedule_planned,
            lean_check_cron=None,
            verification=None,
            provider_install_planned=provider_install_planned,
            provider=None,
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

    provider = _maybe_install_provider(
        provider_install_planned,
        provider_copy_fn=provider_copy_fn,
        provider_install_fn=provider_install_fn,
        install_provider=install_provider,
        apply_failed_early=apply_failed_early,
    )

    # A failed provider bootstrap is itself an early failure: cron and
    # verification must not run against a half-installed environment.
    provider_failed = provider is not None and provider.get("ok") is False
    failed_early = apply_failed_early or provider_failed

    cron = _maybe_create_cron(
        schedule_planned,
        schedule_fn=schedule_fn,
        create_cron=create_cron,
        apply_failed_early=failed_early,
    )

    lean_check_cron = _maybe_create_lean_check_cron(
        lean_check_schedule_planned,
        schedule_fn=lean_check_schedule_fn,
        create_lean_check_cron=create_lean_check_cron,
        apply_failed_early=failed_early,
    )

    verification = _maybe_verify(
        verify_fn=verify_fn,
        verify_after_apply=verify_after_apply,
        apply_failed_early=failed_early,
    )

    return SetupResult(
        applied=True,
        created_directories=created_directories,
        config_commands=config_commands,
        schedule_planned=schedule_planned,
        cron=cron,
        lean_check_schedule_planned=lean_check_schedule_planned,
        lean_check_cron=lean_check_cron,
        verification=verification,
        provider_install_planned=provider_install_planned,
        provider=provider,
        rollback_notes=rollback_notes,
        errors=errors,
    )


def _maybe_install_provider(
    provider_install_planned: dict[str, Any] | None,
    *,
    provider_copy_fn: ProviderCopyFn | None,
    provider_install_fn: ProviderInstallFn | None,
    install_provider: bool,
    apply_failed_early: bool,
) -> dict[str, Any] | None:
    """Bootstrap the real MemPalace provider, or explain why it was not.

    Returns ``None`` when provider install was not requested (the bundle is
    only described via ``provider_install_planned``). Otherwise returns a
    JSON-serializable dict with ``requested``/``ok``/``destination``/
    ``copied_files``/``cli_install``/``error``. Never raises: a copy or CLI
    failure is captured here and surfaces as ``ok=False``.
    """
    if not install_provider:
        return None

    base = {
        "requested": True,
        "ok": False,
        "destination": (provider_install_planned or {}).get("destination"),
        "install_method": (provider_install_planned or {}).get(
            "install_method", "auto"
        ),
        "copied_files": [],
        "cli_install": {
            "argv": None,
            "method": None,
            "ran": False,
            "error": "",
        },
        "attempts": [],
        "error": "",
    }

    if apply_failed_early:
        base["error"] = "skipped: apply failed early; provider not installed"
        return base
    if not provider_install_planned:
        base["error"] = (
            "no provider_install block in plan; pass --install-provider"
        )
        return base
    if provider_install_planned.get("install_method_error"):
        base["error"] = (
            "invalid install method: "
            + provider_install_planned["install_method_error"]
        )
        return base
    if provider_copy_fn is None or provider_install_fn is None:
        base["error"] = (
            "no provider_copy_fn/provider_install_fn injected; "
            "cannot install provider"
        )
        return base

    copied: list[str] = []
    for entry in provider_install_planned.get("files", []):
        source = entry["source"]
        target = entry["target"]
        try:
            provider_copy_fn(source, target)
        except Exception as exc:  # noqa: BLE001 - report, never crash apply
            base["copied_files"] = copied
            base["error"] = f"failed to copy {source!r} -> {target!r}: {exc}"
            return base
        copied.append(target)
    base["copied_files"] = copied

    candidates = _resolve_install_candidates(provider_install_planned)
    if not candidates:
        base["error"] = (
            "provider files copied but no install candidates in plan"
        )
        return base

    attempts: list[dict[str, Any]] = []
    chosen: dict[str, Any] | None = None
    for cand in candidates:
        argv = list(cand["argv"])
        try:
            provider_install_fn(argv)
        except Exception as exc:  # noqa: BLE001 - report, never crash apply
            attempts.append(
                {
                    "method": cand["method"],
                    "argv": argv,
                    "ok": False,
                    "error": str(exc),
                }
            )
            continue
        attempts.append(
            {
                "method": cand["method"],
                "argv": argv,
                "ok": True,
                "error": "",
            }
        )
        chosen = {"method": cand["method"], "argv": argv}
        break

    base["attempts"] = attempts
    if chosen is None:
        summary = "; ".join(
            f"{a['method']}: {a['error']}" for a in attempts
        )
        base["cli_install"]["argv"] = list(candidates[0]["argv"])
        base["cli_install"]["error"] = (
            f"all install methods failed ({summary})"
        )
        base["error"] = "provider files copied but all install methods failed"
        return base

    base["cli_install"]["argv"] = chosen["argv"]
    base["cli_install"]["method"] = chosen["method"]
    base["cli_install"]["ran"] = True
    base["ok"] = True
    return base


def _resolve_install_candidates(
    provider_install_planned: dict[str, Any],
) -> list[dict[str, Any]]:
    """Ordered ``[{"method", "argv"}, ...]`` to try, in fixed order.

    Prefers the explicit ``install_candidates`` emitted by the plan. Falls
    back to a single ``uv`` candidate synthesized from the legacy
    ``cli_install_argv`` so plans built by older code still apply.
    """
    candidates = provider_install_planned.get("install_candidates")
    if candidates:
        return [
            {"method": c["method"], "argv": list(c["argv"])}
            for c in candidates
        ]
    legacy_argv = provider_install_planned.get("cli_install_argv")
    if legacy_argv:
        return [{"method": "uv", "argv": list(legacy_argv)}]
    return []


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


def _maybe_create_lean_check_cron(
    schedule_planned: dict[str, Any] | None,
    *,
    schedule_fn: ScheduleFn | None,
    create_lean_check_cron: bool,
    apply_failed_early: bool,
) -> dict[str, Any] | None:
    """Create the weekly lean-check cron, or explain why it was not.

    Mirrors :func:`_maybe_create_cron` exactly (same safety gating, same
    JSON shape) but for the weekly, live-provider, report-only lean-check
    job. Returns ``None`` when not requested. Never raises.
    """
    if not create_lean_check_cron:
        return None
    if apply_failed_early:
        return {
            "requested": True,
            "created": False,
            "argv": None,
            "error": "skipped: apply failed early; lean-check cron not created",
        }
    if not schedule_planned:
        return {
            "requested": True,
            "created": False,
            "argv": None,
            "error": "no lean_check_schedule in plan; pass --schedule-lean-check",
        }
    if schedule_fn is None:
        return {
            "requested": True,
            "created": False,
            "argv": None,
            "error": "no lean_check_schedule_fn injected; cannot create cron",
        }
    if not schedule_planned.get("cron_utc"):
        reason = schedule_planned.get(
            "timezone_error", "no UTC cron in lean-check schedule"
        )
        return {
            "requested": True,
            "created": False,
            "argv": None,
            "error": f"lean-check cron not created: {reason}",
        }
    argv = build_lean_check_cron_argv(schedule_planned)
    try:
        schedule_fn(argv)
    except Exception as exc:  # noqa: BLE001 - report, never crash apply
        return {
            "requested": True,
            "created": False,
            "argv": argv,
            "error": f"lean-check cron creation failed: {exc}",
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
