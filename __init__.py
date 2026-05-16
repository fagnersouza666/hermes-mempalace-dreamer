
"""Hermes MemPalace Dreaming plugin.

Bootstrap plugin that ships a MemPalace-first dreaming skill and a small CLI
surface for safe setup planning. It intentionally does not mutate config at
import/register time.
"""
from __future__ import annotations

import dataclasses
import json
import re
import subprocess
from datetime import datetime, timezone as _utc_tz
from pathlib import Path
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VerifyRunFn = Callable[[Sequence[str]], dict]

#: Honest, deterministic default. The scheduler interprets cron in UTC, so
#: when no ``--timezone`` is given the requested time is treated as UTC --
#: never silently as "local time".
DEFAULT_TIMEZONE = "UTC"

#: Fixed reference date used to resolve a timezone's UTC offset. A daily cron
#: fires at one fixed UTC instant; for zones that observe DST the local run
#: time shifts by the DST delta during the opposite part of the year. This
#: keeps conversion deterministic (independent of "today") and the caveat is
#: documented in the plan output and the READMEs.
_CRON_REFERENCE_DATE = (2025, 1, 15)


def _format_utc_offset(offset) -> str:
    """Render a ``timedelta`` UTC offset as ``+HH:MM`` / ``-HH:MM``."""
    total = int(offset.total_seconds()) if offset is not None else 0
    sign = "-" if total < 0 else "+"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def convert_to_utc_cron(
    time: str, timezone: str = DEFAULT_TIMEZONE
) -> dict[str, Any]:
    """Convert a wall-clock ``"HH:MM"`` in ``timezone`` to a UTC daily cron.

    Returns the requested time/timezone alongside the resulting UTC time and
    the daily cron expression ``"MM HH * * *"`` (UTC), plus the UTC offset
    used and a DST caveat. Raises :class:`zoneinfo.ZoneInfoNotFoundError` for
    an unknown timezone and ``ValueError`` for an out-of-range ``HH:MM`` (the
    latter mirrors the pre-existing validation behavior).
    """
    hour_str, _, minute_str = time.partition(":")
    hour = int(hour_str)
    minute = int(minute_str)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time out of range: {time!r}")

    tz = ZoneInfo(timezone)  # ZoneInfoNotFoundError on an unknown zone
    year, month, day = _CRON_REFERENCE_DATE
    local_dt = datetime(year, month, day, hour, minute, tzinfo=tz)
    utc_dt = local_dt.astimezone(_utc_tz.utc)

    return {
        "requested_time": time,
        "timezone": timezone,
        "utc_time": f"{utc_dt.hour:02d}:{utc_dt.minute:02d}",
        "cron_utc": f"{utc_dt.minute:02d} {utc_dt.hour:02d} * * *",
        "utc_offset": _format_utc_offset(local_dt.utcoffset()),
        "dst_caveat": (
            "Cron fires at a fixed UTC instant. For zones that observe DST "
            "the wall-clock run time shifts by the DST delta during the "
            "opposite part of the year."
        ),
    }

PLUGIN_DIR = Path(__file__).resolve().parent
SKILL_PATH = PLUGIN_DIR / "skills" / "mempalace-dreaming" / "SKILL.md"
PLUGIN_NAME = "mempalace-dreaming"
PLUGIN_VERSION = "1.0.0"
PLUGIN_STATUS = "production-ready bootstrap v1.0"


def build_schedule_plan(
    time: str = "05:30", timezone: str = DEFAULT_TIMEZONE
) -> dict[str, Any]:
    """Return a report-only daily dreaming schedule plan.

    This describes *what* a conservative daily dreaming cron would look like.
    It is purely informational: nothing here creates a cron job. The plan
    shows both the requested wall-clock ``time``/``timezone`` and the
    resulting UTC cron expression (the scheduler runs cron in UTC). An
    unknown timezone becomes a ``warnings`` entry instead of a traceback;
    no cron is computed in that case.
    """
    plan: dict[str, Any] = {
        "name": "MemPalace Dreaming",
        "time": time,
        "timezone": timezone,
        "prompt_profile": "daily-conservative",
        "skill": "plugin:mempalace-dreaming",
        "report_only": True,
        "note": (
            "Report-only: no cron job is created. Schedule this manually "
            "via your Hermes cron tooling if you want daily dreaming."
        ),
        "warnings": [],
    }
    try:
        conv = convert_to_utc_cron(time, timezone)
    except ZoneInfoNotFoundError as exc:
        plan["warnings"].append(
            f"unknown timezone {timezone!r} ({exc}); no UTC cron computed"
        )
        return plan
    plan["utc_time"] = conv["utc_time"]
    plan["cron_utc"] = conv["cron_utc"]
    plan["utc_offset"] = conv["utc_offset"]
    plan["dst_caveat"] = conv["dst_caveat"]
    return plan


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
            "cron_creation_explicit": True,
            "verify_after_apply_explicit": True,
        },
    }


def build_setup_plan(hermes_home: str | Path, schedule_dreaming: bool = False, time: str = "05:30", timezone: str = DEFAULT_TIMEZONE) -> dict[str, Any]:
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
        schedule: dict[str, Any] = {
            "name": "MemPalace Dreaming",
            "time": time,
            "timezone": timezone,
            "prompt_profile": "daily-conservative",
            "skill": "plugin:mempalace-dreaming",
        }
        try:
            conv = convert_to_utc_cron(time, timezone)
        except ZoneInfoNotFoundError as exc:
            # No misleading cron is emitted; the apply layer surfaces this
            # as a non-created cron with an error instead of crashing.
            schedule["timezone_error"] = (
                f"unknown timezone {timezone!r} ({exc}); no UTC cron computed"
            )
        else:
            schedule["utc_time"] = conv["utc_time"]
            schedule["cron_utc"] = conv["cron_utc"]
            schedule["utc_offset"] = conv["utc_offset"]
            schedule["dst_caveat"] = conv["dst_caveat"]
        plan["schedule"] = schedule
    return plan


def _default_verify_run(argv: Sequence[str]) -> dict:
    """Run ``argv`` read-only and never raise.

    Captures the outcome as a JSON-serializable dict. A missing binary, a
    timeout, or any other failure is returned as ``ok=False`` with an
    ``error`` string instead of propagating an exception. This only ever
    reads (``hermes --version`` / ``hermes memory status``); it mutates
    nothing.
    """
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "error": "",
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": f"command not found: {exc}",
        }
    except Exception as exc:  # noqa: BLE001 - report, never crash verify
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": str(exc),
        }


_PROVIDER_RE = re.compile(
    r"\bprovider\b\s*[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)


def _detect_memory_provider(stdout: str) -> str | None:
    """Best-effort, lowercase memory provider name from status output.

    Tolerant and side-effect free: tries JSON first (top-level ``provider``
    or a nested ``memory.provider``), then falls back to a permissive regex
    over plain text. Returns ``None`` when nothing provider-like is found.
    Never raises.
    """
    if not stdout or not stdout.strip():
        return None
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        provider = data.get("provider")
        if provider is None and isinstance(data.get("memory"), dict):
            provider = data["memory"].get("provider")
        if isinstance(provider, str) and provider.strip():
            return provider.strip().lower()
    match = _PROVIDER_RE.search(stdout)
    if match:
        return match.group(1).strip().lower()
    return None


def build_runtime_verification(
    hermes_home: str | Path = "~/.hermes",
    *,
    run_fn: VerifyRunFn = _default_verify_run,
) -> dict[str, Any]:
    """Read-only verification of the live Hermes environment.

    Pure with respect to side effects: it only *reads* (runs ``hermes
    --version`` and ``hermes memory status`` via ``run_fn``, stats files)
    and never mutates config, memory, cron, or the filesystem. ``run_fn`` is
    injected for testability and defaults to :func:`_default_verify_run`.

    Any failure from ``run_fn`` (including an injected one that raises) is
    captured in the returned JSON-serializable dict rather than propagated.
    The result always carries a top-level ``ok`` boolean and a ``warnings``
    list.
    """

    def _safe_run(argv: Sequence[str]) -> dict:
        try:
            return run_fn(argv)
        except Exception as exc:  # noqa: BLE001 - capture, never raise
            return {
                "ok": False,
                "returncode": -1,
                "stdout": "",
                "stderr": "",
                "error": str(exc),
            }

    version_res = _safe_run(["hermes", "--version"])
    status_res = _safe_run(["hermes", "memory", "status"])

    hermes_cli_callable = bool(version_res.get("ok"))
    memory_status_ok = bool(status_res.get("ok"))
    memory_provider = (
        _detect_memory_provider(status_res.get("stdout", ""))
        if memory_status_ok
        else None
    )
    provider_is_mempalace = memory_provider == "mempalace"

    skill_exists = SKILL_PATH.is_file()
    engine_ok = _module_importable("mempalace_dreaming.engine")
    setup_ok = _module_importable("mempalace_dreaming.setup")

    plan = build_setup_plan(hermes_home=hermes_home)
    directories = [
        {"path": path, "exists": Path(path).expanduser().exists()}
        for path in plan["directories"]
    ]
    all_directories_exist = all(d["exists"] for d in directories)

    warnings: list[str] = []
    if not hermes_cli_callable:
        detail = version_res.get("error") or (
            f"exit {version_res.get('returncode')}"
        )
        warnings.append(f"hermes CLI is not callable: {detail}")
    if not memory_status_ok:
        detail = status_res.get("error") or (
            f"exit {status_res.get('returncode')}"
        )
        warnings.append(f"'hermes memory status' failed: {detail}")
    elif not provider_is_mempalace:
        warnings.append(
            "memory provider is not 'mempalace' "
            f"(detected: {memory_provider!r})"
        )
    if not skill_exists:
        warnings.append("bundled skill file is missing")
    if not engine_ok:
        warnings.append("mempalace_dreaming.engine is not available")
    if not setup_ok:
        warnings.append("mempalace_dreaming.setup is not available")
    if not all_directories_exist:
        missing = [d["path"] for d in directories if not d["exists"]]
        warnings.append(
            "expected mempalace directories are missing: "
            + ", ".join(missing)
        )

    ok = (
        hermes_cli_callable
        and memory_status_ok
        and provider_is_mempalace
        and skill_exists
        and engine_ok
        and setup_ok
        and all_directories_exist
    )

    return {
        "plugin": PLUGIN_NAME,
        "version": PLUGIN_VERSION,
        "hermes_home": str(Path(hermes_home).expanduser()),
        "checks": {
            "hermes_cli_callable": hermes_cli_callable,
            "memory_status_ok": memory_status_ok,
            "memory_provider": memory_provider,
            "provider_is_mempalace": provider_is_mempalace,
            "bundled_skill_exists": skill_exists,
            "engine_module_available": engine_ok,
            "setup_module_available": setup_ok,
            "directories": directories,
            "all_directories_exist": all_directories_exist,
        },
        "commands": {
            "hermes_version": {
                "ok": bool(version_res.get("ok")),
                "returncode": version_res.get("returncode"),
                "error": version_res.get("error", ""),
            },
            "memory_status": {
                "ok": bool(status_res.get("ok")),
                "returncode": status_res.get("returncode"),
                "error": status_res.get("error", ""),
            },
        },
        "ok": ok,
        "warnings": warnings,
    }


def _setup_cli_parser(parser) -> None:
    sub = parser.add_subparsers(dest="mempalace_dreaming_command")
    plan = sub.add_parser("setup-plan", help="Print a safe MemPalace Dreaming setup plan")
    plan.add_argument("--hermes-home", default="~/.hermes", help="Hermes home directory")
    plan.add_argument("--schedule-dreaming", action="store_true", help="Include optional dreaming cron plan")
    plan.add_argument("--time", default="05:30", help="Wall-clock time (HH:MM) for the optional dreaming cron, interpreted in --timezone")
    plan.add_argument("--timezone", default=DEFAULT_TIMEZONE, help=f"IANA timezone for --time (default: {DEFAULT_TIMEZONE}; the cron is converted to UTC)")
    plan.set_defaults(func=_handle_cli)

    setup = sub.add_parser(
        "setup",
        help="Dry-run (default) or --apply the MemPalace Dreaming setup",
    )
    setup.add_argument("--hermes-home", default="~/.hermes", help="Hermes home directory")
    setup.add_argument("--schedule-dreaming", action="store_true", help="Include optional dreaming cron plan (report-only)")
    setup.add_argument("--time", default="05:30", help="Wall-clock time (HH:MM) for the optional dreaming cron, interpreted in --timezone")
    setup.add_argument("--timezone", default=DEFAULT_TIMEZONE, help=f"IANA timezone for --time (default: {DEFAULT_TIMEZONE}; the cron is converted to UTC)")
    setup.add_argument(
        "--apply",
        action="store_true",
        help="Create directories and run 'hermes config set ...' (default is dry-run)",
    )
    setup.add_argument(
        "--create-cron",
        action="store_true",
        help=(
            "With --apply, also create the daily dreaming cron via "
            "'hermes cron create' (deterministic name, deliver=local). "
            "Without this flag scheduling stays report-only."
        ),
    )
    setup.add_argument(
        "--verify-after-apply",
        action="store_true",
        help=(
            "With --apply, run a read-only runtime verification afterwards "
            "and include it in the JSON (skipped if apply failed early)."
        ),
    )
    setup.set_defaults(func=_handle_cli)

    status = sub.add_parser(
        "status",
        help="Print plugin status and safety flags as JSON (read-only)",
    )
    status.set_defaults(func=_handle_cli)

    verify = sub.add_parser(
        "verify-runtime",
        help="Read-only live environment check, JSON only (no side effects)",
    )
    verify.add_argument(
        "--hermes-home", default="~/.hermes", help="Hermes home directory"
    )
    verify.set_defaults(func=_handle_cli)

    schedule_plan = sub.add_parser(
        "schedule-plan",
        help="Print a report-only daily dreaming schedule plan (no cron)",
    )
    schedule_plan.add_argument(
        "--time", default="05:30", help="Wall-clock time (HH:MM) for the planned dreaming run, interpreted in --timezone"
    )
    schedule_plan.add_argument(
        "--timezone", default=DEFAULT_TIMEZONE, help=f"IANA timezone for --time (default: {DEFAULT_TIMEZONE}; the cron is converted to UTC)"
    )
    schedule_plan.set_defaults(func=_handle_cli)

    lean_check = sub.add_parser(
        "lean-check",
        help="Report-only lean-check of candidate memory material (no writes)",
    )
    lean_check.add_argument(
        "--input-file",
        default=None,
        help="Path to a file with one candidate/retrieval text per line",
    )
    lean_check.add_argument(
        "--json-input",
        default=None,
        help='JSON array of candidate texts (or {"text": ...} objects)',
    )
    lean_check.set_defaults(func=_handle_cli)


def _default_mkdir(path: str) -> None:
    Path(path).expanduser().mkdir(parents=True, exist_ok=True)


def _default_run(argv) -> None:
    subprocess.run(list(argv), check=True)


def _default_schedule(argv) -> None:
    """Create a cron job by running an argv list (never a shell string).

    Used as the default ``schedule_fn`` for ``setup --apply --create-cron``.
    Raises ``CalledProcessError`` on failure, which the apply layer captures
    into the JSON ``cron`` result instead of crashing.
    """
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


def _apply_setup_from_args(
    args,
    *,
    mkdir_fn=_default_mkdir,
    run_fn=_default_run,
    schedule_fn=_default_schedule,
    verify_fn=None,
):
    """Build the plan from CLI args and apply (or describe) it.

    Factored out of :func:`_handle_cli` so apply mode is unit-testable
    without running Hermes: inject ``mkdir_fn`` / ``run_fn`` /
    ``schedule_fn`` / ``verify_fn``. Cron creation and post-apply
    verification only happen when their explicit flags are set *and*
    ``--apply`` is set; both are dependency-injected (no hidden globals).
    The default ``verify_fn`` is a read-only closure over
    :func:`build_runtime_verification` for the chosen ``--hermes-home``.
    """
    apply_setup_plan = _load_apply_setup_plan()

    hermes_home = getattr(args, "hermes_home", "~/.hermes")
    plan = build_setup_plan(
        hermes_home=hermes_home,
        schedule_dreaming=getattr(args, "schedule_dreaming", False),
        time=getattr(args, "time", "05:30"),
        timezone=getattr(args, "timezone", DEFAULT_TIMEZONE),
    )
    if verify_fn is None:
        def verify_fn() -> dict:
            return build_runtime_verification(hermes_home=hermes_home)

    return apply_setup_plan(
        plan,
        mkdir_fn=mkdir_fn,
        run_fn=run_fn,
        apply=getattr(args, "apply", False),
        schedule_fn=schedule_fn,
        create_cron=getattr(args, "create_cron", False),
        verify_fn=verify_fn,
        verify_after_apply=getattr(args, "verify_after_apply", False),
    )


def _load_build_lean_check_report():
    """Resolve ``build_lean_check_report`` without depending on ``sys.path``.

    Mirrors :func:`_load_apply_setup_plan`: normal package import first, then
    a direct load of ``mempalace_dreaming/engine.py`` from :data:`PLUGIN_DIR`
    when the plugin file is loaded standalone.
    """
    try:
        from mempalace_dreaming.engine import build_lean_check_report

        return build_lean_check_report
    except ImportError:
        import importlib.util
        import sys

        engine_path = PLUGIN_DIR / "mempalace_dreaming" / "engine.py"
        spec = importlib.util.spec_from_file_location(
            "mempalace_dreaming_engine", engine_path
        )
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module.build_lean_check_report


def _coerce_lean_check_inputs(data: object) -> list[object]:
    """Accept a JSON array of strings or ``{"text": ...}`` objects."""
    if isinstance(data, list):
        return list(data)
    return []


def _read_lean_check_inputs(args) -> tuple[list[object], list[str]]:
    """Build the candidate list from CLI args, never raising.

    Returns ``(candidates, warnings)``. A missing input file or invalid JSON
    becomes a warning and an empty list -- the command stays report-only and
    never crashes. Reads only; writes nothing.
    """
    warnings: list[str] = []

    json_input = getattr(args, "json_input", None)
    if json_input:
        try:
            return _coerce_lean_check_inputs(json.loads(json_input)), warnings
        except (ValueError, TypeError) as exc:
            warnings.append(f"--json-input is not valid JSON: {exc}")
            return [], warnings

    input_file = getattr(args, "input_file", None)
    if input_file:
        path = Path(input_file).expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append(f"input file not found / unreadable: {exc}")
            return [], warnings
        lines = [line.strip() for line in text.splitlines()]
        return [line for line in lines if line], warnings

    return [], warnings


def _handle_cli(args) -> None:
    cmd = getattr(args, "mempalace_dreaming_command", None)
    if cmd == "status":
        print(json.dumps(_build_status(), indent=2, ensure_ascii=False))
        return
    if cmd == "verify-runtime":
        verification = build_runtime_verification(
            hermes_home=getattr(args, "hermes_home", "~/.hermes")
        )
        print(json.dumps(verification, indent=2, ensure_ascii=False))
        return
    if cmd == "schedule-plan":
        plan = build_schedule_plan(
            time=getattr(args, "time", "05:30"),
            timezone=getattr(args, "timezone", DEFAULT_TIMEZONE),
        )
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return
    if cmd == "lean-check":
        build_lean_check_report = _load_build_lean_check_report()
        candidates, input_warnings = _read_lean_check_inputs(args)
        report = build_lean_check_report(
            candidates, extra_warnings=input_warnings
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
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
            timezone=getattr(args, "timezone", DEFAULT_TIMEZONE),
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
