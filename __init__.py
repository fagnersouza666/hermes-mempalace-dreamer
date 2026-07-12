
"""Hermes MemPalace Dreaming plugin.

Bootstrap plugin that ships a MemPalace-first dreaming skill and a small CLI
surface for safe setup planning. It intentionally does not mutate config at
import/register time.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone as _utc_tz
from pathlib import Path
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VerifyRunFn = Callable[[Sequence[str]], dict]

#: Honest, deterministic default. The scheduler interprets cron in UTC, so
#: when no ``--timezone`` is given the requested time is treated as UTC --
#: never silently as "local time".
DEFAULT_TIMEZONE = "UTC"
PROFILE_MODE_INTERACTIVE = "interactive"
PROFILE_MODE_EXTRACTION = "extraction"
PROFILE_MODE_AUTO = "auto"
PROFILE_MODES = (PROFILE_MODE_INTERACTIVE, PROFILE_MODE_EXTRACTION)
PROFILE_MODES_WITH_AUTO = (PROFILE_MODE_AUTO,) + PROFILE_MODES

#: Fixed reference date used to resolve a timezone's UTC offset. A daily cron
#: fires at one fixed UTC instant; for zones that observe DST the local run
#: time shifts by the DST delta during the opposite part of the year. This
#: keeps conversion deterministic (independent of "today") and the caveat is
#: documented in the plan output and the READMEs.
_CRON_REFERENCE_DATE = (2025, 1, 15)


def _default_hermes_home() -> str:
    """Return the active Hermes home, honoring ``$HERMES_HOME`` when present.

    Runtime validation exposed a real-host mismatch: hardcoding ``~/.hermes``
    makes CLI commands ignore an isolated/fresh ``HERMES_HOME`` even though the
    host Hermes runtime itself respects that environment variable. Prefer the
    host helper when available; otherwise fall back to the env var and finally
    to the conventional default path.
    """

    try:
        from hermes_constants import get_hermes_home

        return str(get_hermes_home())
    except Exception:  # noqa: BLE001 - keep plugin importable outside Hermes
        return os.environ.get("HERMES_HOME", "~/.hermes")


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
        # -1 / 0 / +1: the UTC instant can land on the previous or next day
        # relative to the local wall-clock day. Weekly crons use this to keep
        # the day-of-week field honest after the UTC conversion.
        "day_offset": (utc_dt.date() - local_dt.date()).days,
        "dst_caveat": (
            "Cron fires at a fixed UTC instant. For zones that observe DST "
            "the wall-clock run time shifts by the DST delta during the "
            "opposite part of the year."
        ),
    }

#: Default weekday for the weekly lean-check cron. Cron DOW convention:
#: 0 = Sunday ... 6 = Saturday. Sunday keeps it off the work week.
_LEAN_CHECK_DEFAULT_WEEKDAY = 0


def convert_to_utc_weekly_cron(
    time: str, timezone: str = DEFAULT_TIMEZONE, weekday: int = _LEAN_CHECK_DEFAULT_WEEKDAY
) -> dict[str, Any]:
    """Convert a weekly wall-clock ``"HH:MM"`` + weekday to a UTC weekly cron.

    Reuses :func:`convert_to_utc_cron` for the time conversion, then shifts
    the cron day-of-week field by the UTC ``day_offset`` so the job still
    fires on the intended local weekday. Returns the daily fields plus
    ``requested_weekday``, ``utc_weekday`` and a weekly ``cron_utc``
    (``"MM HH * * D"``). Raises the same errors as
    :func:`convert_to_utc_cron`; ``ValueError`` for a weekday outside 0..6.
    """
    if not (0 <= weekday <= 6):
        raise ValueError(f"weekday out of range (expected 0..6): {weekday!r}")
    conv = convert_to_utc_cron(time, timezone)
    utc_weekday = (weekday + conv["day_offset"]) % 7
    minute, hour = conv["cron_utc"].split()[:2]
    conv = dict(conv)
    conv["requested_weekday"] = weekday
    conv["utc_weekday"] = utc_weekday
    conv["cron_utc"] = f"{minute} {hour} * * {utc_weekday}"
    return conv


PLUGIN_DIR = Path(__file__).resolve().parent
SKILL_PATH = PLUGIN_DIR / "skills" / "mempalace-dreaming" / "SKILL.md"

#: Bundled MemPalace provider artifacts. Profile-safe (no absolute home
#: hardcode); copied verbatim into ``$HERMES_HOME/plugins/mempalace/`` only
#: behind the explicit ``setup --apply --install-provider`` flag. Stored as
#: ``provider_init.py`` so it is never auto-imported here (it imports the
#: Hermes runtime ``agent.memory_provider``); it lands as ``__init__.py``.
PROVIDER_BUNDLE_DIR = PLUGIN_DIR / "mempalace_dreaming" / "provider_bundle"
PROVIDER_BUNDLE_FILES = (
    ("provider_init.py", "__init__.py"),
    ("plugin.yaml", "plugin.yaml"),
)
#: Isolated CLI install (no shell). Upgrades in place if already present.
#: Kept for backward compatibility / legacy callers; the canonical source is
#: :func:`_provider_install_argv` keyed by method.
PROVIDER_CLI_INSTALL_ARGV = [
    "uv",
    "tool",
    "install",
    "--upgrade",
    "mempalace",
]

#: Deterministic fallback order for the ``auto`` install method. The first
#: candidate that succeeds wins; the order is fixed (never reshuffled) so the
#: same environment always resolves the same way and the plan/JSON can show
#: exactly what would be attempted.
PROVIDER_INSTALL_METHOD_ORDER = ("uv", "pipx", "pip-user")

#: Every accepted ``--install-method`` value. ``auto`` walks
#: :data:`PROVIDER_INSTALL_METHOD_ORDER`; the rest pin a single tool.
PROVIDER_INSTALL_METHODS = ("auto",) + PROVIDER_INSTALL_METHOD_ORDER


def _provider_install_argv(method: str) -> list[str]:
    """Return the exact argv (never a shell string) for one concrete method.

    ``pip-user`` uses :data:`sys.executable` (the interpreter Hermes runs
    under) so the install lands in the right user site for *this* Python,
    instead of guessing a ``python``/``python3`` on PATH. ``pipx install
    --force`` reinstalls/upgrades in place. Raises ``ValueError`` for an
    unknown method so callers can capture it as a plan-level error.
    """
    if method == "uv":
        return ["uv", "tool", "install", "--upgrade", "mempalace"]
    if method == "pipx":
        return ["pipx", "install", "--force", "mempalace"]
    if method == "pip-user":
        return [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--user",
            "--upgrade",
            "mempalace",
        ]
    raise ValueError(f"unknown provider install method: {method!r}")

PLUGIN_NAME = "mempalace-dreaming"
PLUGIN_VERSION = "1.1.2"
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
        "skill": "mempalace-dreaming:mempalace-dreaming",
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
            "provider_install_explicit": True,
        },
    }


def _build_provider_install_block(
    home: Path, install_method: str = "auto"
) -> dict[str, Any]:
    """Describe the explicit MemPalace provider bootstrap (report-only).

    Maps each bundled artifact to a profile-safe target under
    ``$HERMES_HOME/plugins/mempalace/`` and resolves the ordered install
    candidates for ``install_method``. ``auto`` expands to the fixed
    :data:`PROVIDER_INSTALL_METHOD_ORDER` (uv -> pipx -> pip-user); a pinned
    method yields a single candidate. Pure: it only resolves paths/argv and
    never copies, installs, or mutates anything. An unknown method is
    captured as ``install_method_error`` (no candidates) instead of raising,
    mirroring how an unknown timezone degrades the schedule plan.
    """
    destination = home / "plugins" / "mempalace"
    block: dict[str, Any] = {
        "destination": str(destination),
        "install_method": install_method,
        "files": [
            {
                "source": str(PROVIDER_BUNDLE_DIR / src),
                "target": str(destination / dst),
            }
            for src, dst in PROVIDER_BUNDLE_FILES
        ],
    }
    if install_method not in PROVIDER_INSTALL_METHODS:
        block["install_method_error"] = (
            f"unknown install method {install_method!r}; expected one of "
            + ", ".join(PROVIDER_INSTALL_METHODS)
        )
        block["install_candidates"] = []
        block["cli_install_argv"] = []
        block["notes"] = [
            "No install candidates: the requested method is invalid.",
            "Only effective with 'setup --apply --install-provider'; dry-run "
            "just prints this block.",
        ]
        return block

    methods = (
        list(PROVIDER_INSTALL_METHOD_ORDER)
        if install_method == "auto"
        else [install_method]
    )
    candidates = [
        {"method": m, "argv": _provider_install_argv(m)} for m in methods
    ]
    block["install_candidates"] = candidates
    # Backward-compatible key: the first (preferred) candidate's argv. Older
    # callers / tests read ``cli_install_argv`` directly.
    block["cli_install_argv"] = list(candidates[0]["argv"])
    order_text = " -> ".join(c["method"] for c in candidates)
    block["notes"] = [
        "Provider artifacts are copied verbatim; paths stay profile-safe "
        "($HERMES_HOME / ~), no absolute home is hardcoded.",
        f"Install method '{install_method}'; candidates tried in fixed "
        f"order: {order_text}. The first that succeeds wins; argv lists "
        "only, never a shell string.",
        "Only effective with 'setup --apply --install-provider'; dry-run "
        "just prints this block.",
    ]
    return block


def _build_lean_check_schedule(
    time: str, timezone: str, weekday: int
) -> dict[str, Any]:
    """Describe the weekly, live-provider lean-check cron (report-only).

    Pure: only resolves the UTC weekly cron expression. An unknown timezone
    or out-of-range weekday is captured as a ``timezone_error`` so the apply
    layer reports a non-created cron instead of crashing.
    """
    schedule: dict[str, Any] = {
        "name": "MemPalace Dreaming weekly lean-check",
        "time": time,
        "timezone": timezone,
        "weekday": weekday,
        "prompt_profile": "weekly-lean-check-live",
        "skill": "mempalace-dreaming:mempalace-dreaming",
    }
    try:
        conv = convert_to_utc_weekly_cron(time, timezone, weekday)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        schedule["timezone_error"] = (
            f"weekly lean-check schedule not computed ({exc})"
        )
        return schedule
    schedule["utc_time"] = conv["utc_time"]
    schedule["cron_utc"] = conv["cron_utc"]
    schedule["utc_offset"] = conv["utc_offset"]
    schedule["utc_weekday"] = conv["utc_weekday"]
    schedule["dst_caveat"] = conv["dst_caveat"]
    return schedule


def _normalize_profile_mode(profile_mode: str, *, allow_auto: bool = False) -> str:
    normalized = str(profile_mode).strip().lower()
    allowed = PROFILE_MODES_WITH_AUTO if allow_auto else PROFILE_MODES
    if normalized not in allowed:
        expected = ", ".join(allowed)
        raise ValueError(f"invalid profile_mode {profile_mode!r}; expected one of: {expected}")
    return normalized


def _build_profile_config(profile_mode: str) -> dict[str, object]:
    mode = _normalize_profile_mode(profile_mode)
    return {
        "memory.memory_enabled": mode == PROFILE_MODE_INTERACTIVE,
        "memory.user_profile_enabled": mode == PROFILE_MODE_INTERACTIVE,
        "memory.provider": "mempalace",
        "plugins.mempalace_dreaming.enabled": True,
        "plugins.mempalace_dreaming.skill": "mempalace-dreaming:mempalace-dreaming",
    }


def _infer_profile_mode_from_config(config_data: dict | None) -> str:
    if not isinstance(config_data, dict):
        return PROFILE_MODE_INTERACTIVE
    found_memory, memory_enabled = _lookup_dotted(config_data, "memory.memory_enabled")
    found_profile, user_profile_enabled = _lookup_dotted(
        config_data, "memory.user_profile_enabled"
    )
    if found_memory and found_profile and not bool(memory_enabled) and not bool(user_profile_enabled):
        return PROFILE_MODE_EXTRACTION
    return PROFILE_MODE_INTERACTIVE


def build_setup_plan(hermes_home: str | Path, schedule_dreaming: bool = False, time: str = "05:30", timezone: str = DEFAULT_TIMEZONE, install_provider: bool = False, install_method: str = "auto", schedule_lean_check: bool = False, lean_check_time: str = "06:30", lean_check_weekday: int = _LEAN_CHECK_DEFAULT_WEEKDAY, profile_mode: str = PROFILE_MODE_INTERACTIVE) -> dict[str, Any]:
    """Return an idempotent setup plan without applying it.

    The real installer can consume this structure to print a diff, create
    directories, set Hermes config keys, and optionally create a cron job.
    Keeping this pure makes it testable and safe.
    """
    home = Path(hermes_home).expanduser()
    resolved_profile_mode = _normalize_profile_mode(profile_mode)
    plan: dict[str, Any] = {
        "profile_mode": resolved_profile_mode,
        "directories": [
            str(home / "mempalace" / "palace"),
            str(home / "mempalace" / "hermes-corpus"),
        ],
        "config": _build_profile_config(resolved_profile_mode),
        "skill": {
            "name": "mempalace-dreaming",
            "qualified_name": "mempalace-dreaming:mempalace-dreaming",
            "path": str(SKILL_PATH),
        },
        "notes": [
            "Bootstrap the real MemPalace provider explicitly with 'setup --apply --install-provider' (opt-in), or install/enable a MemoryProvider separately.",
            f"Profile mode: {resolved_profile_mode}. Interactive mode keeps built-in memory/user profile on; extraction mode keeps the Hermes core stock and disables those built-ins in this profile.",
            "Restart Hermes or start a fresh session after config changes.",
            "Dreaming cron is opt-in; daily automation must be conservative and report-first.",
            "Do NOT schedule built-in short-memory (MEMORY.md/USER.md) cleanup as a cron job: Hermes cron sessions run with skip_memory=True, so the memory tool is unavailable and the job claims success without effect (upstream NousResearch/hermes-agent#9763). Run that cleanup interactively; `doctor` detects and reports such jobs.",
        ],
    }
    if schedule_dreaming:
        schedule: dict[str, Any] = {
            "name": "MemPalace Dreaming",
            "time": time,
            "timezone": timezone,
            "prompt_profile": "daily-conservative",
            "skill": "mempalace-dreaming:mempalace-dreaming",
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
    if schedule_lean_check:
        plan["lean_check_schedule"] = _build_lean_check_schedule(
            lean_check_time, timezone, lean_check_weekday
        )
    if install_provider:
        plan["provider_install"] = _build_provider_install_block(
            home, install_method
        )
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
    hermes_home: str | Path | None = None,
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

    hermes_home = hermes_home or _default_hermes_home()

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
    plan.add_argument("--hermes-home", default=_default_hermes_home(), help="Hermes home directory")
    plan.add_argument("--schedule-dreaming", action="store_true", help="Include optional dreaming cron plan")
    plan.add_argument("--time", default="05:30", help="Wall-clock time (HH:MM) for the optional dreaming cron, interpreted in --timezone")
    plan.add_argument("--timezone", default=DEFAULT_TIMEZONE, help=f"IANA timezone for --time (default: {DEFAULT_TIMEZONE}; the cron is converted to UTC)")
    plan.add_argument(
        "--install-provider",
        action="store_true",
        help="Include the explicit MemPalace provider bootstrap plan (report-only)",
    )
    plan.add_argument(
        "--install-method",
        choices=PROVIDER_INSTALL_METHODS,
        default="auto",
        help=(
            "Provider CLI install strategy: 'auto' (uv -> pipx -> pip-user, "
            "first that works), or pin 'uv' / 'pipx' / 'pip-user'. "
            "Reflected in the plan JSON (default: auto)."
        ),
    )
    plan.add_argument(
        "--profile-mode",
        choices=PROFILE_MODES,
        default=PROFILE_MODE_INTERACTIVE,
        help=(
            "Config target: 'interactive' keeps built-in memory/user-profile "
            "enabled in this profile; 'extraction' disables those built-ins "
            "for a stock-core split-profile setup."
        ),
    )
    plan.add_argument(
        "--schedule-lean-check",
        action="store_true",
        help="Include the weekly live-provider lean-check cron plan (report-only)",
    )
    plan.add_argument(
        "--lean-check-time",
        default="06:30",
        help="Wall-clock time (HH:MM) for the weekly lean-check cron, interpreted in --timezone",
    )
    plan.add_argument(
        "--lean-check-weekday",
        type=int,
        default=_LEAN_CHECK_DEFAULT_WEEKDAY,
        help="Cron day-of-week for the weekly lean-check (0=Sunday..6=Saturday; default: 0)",
    )
    plan.set_defaults(func=_handle_cli)

    setup = sub.add_parser(
        "setup",
        help="Dry-run (default) or --apply the MemPalace Dreaming setup",
    )
    setup.add_argument("--hermes-home", default=_default_hermes_home(), help="Hermes home directory")
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
    setup.add_argument(
        "--install-provider",
        action="store_true",
        help=(
            "Include the explicit MemPalace provider bootstrap in the plan; "
            "with --apply, copy the bundled provider into $HERMES_HOME/plugins/mempalace/ "
            "and install the 'mempalace' CLI via --install-method."
        ),
    )
    setup.add_argument(
        "--install-method",
        choices=PROVIDER_INSTALL_METHODS,
        default="auto",
        help=(
            "Provider CLI install strategy used with --install-provider: "
            "'auto' tries uv -> pipx -> pip-user (first that succeeds wins), "
            "or pin 'uv' / 'pipx' / 'pip-user'. Exposed in the result JSON "
            "(default: auto)."
        ),
    )
    setup.add_argument(
        "--profile-mode",
        choices=PROFILE_MODES,
        default=PROFILE_MODE_INTERACTIVE,
        help=(
            "Config target: 'interactive' keeps built-in memory/user-profile "
            "enabled in this profile; 'extraction' disables those built-ins "
            "for a stock-core split-profile setup."
        ),
    )
    setup.add_argument(
        "--schedule-lean-check",
        action="store_true",
        help="Include the weekly live-provider lean-check cron plan (report-only)",
    )
    setup.add_argument(
        "--lean-check-time",
        default="06:30",
        help="Wall-clock time (HH:MM) for the weekly lean-check cron, interpreted in --timezone",
    )
    setup.add_argument(
        "--lean-check-weekday",
        type=int,
        default=_LEAN_CHECK_DEFAULT_WEEKDAY,
        help="Cron day-of-week for the weekly lean-check (0=Sunday..6=Saturday; default: 0)",
    )
    setup.add_argument(
        "--create-lean-check-cron",
        action="store_true",
        help=(
            "With --apply, also create the weekly lean-check cron via "
            "'hermes cron create' (deterministic name, deliver=local, "
            "report-only prompt against the live provider). Without this "
            "flag the lean-check schedule stays report-only."
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
        "--hermes-home", default=_default_hermes_home(), help="Hermes home directory"
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

    doctor = sub.add_parser(
        "doctor",
        help="Read-only operational audit: plugin/memory/config/cron (no side effects)",
    )
    doctor.add_argument(
        "--hermes-home", default=_default_hermes_home(), help="Hermes home directory"
    )
    doctor.add_argument(
        "--expected-time",
        default=None,
        help="Expected wall-clock time (HH:MM) to compare against the live cron schedule",
    )
    doctor.add_argument(
        "--timezone",
        default=None,
        help="IANA timezone for --expected-time conversion (omit to skip schedule comparison)",
    )
    doctor.add_argument(
        "--profile-mode",
        choices=PROFILE_MODES_WITH_AUTO,
        default=PROFILE_MODE_AUTO,
        help=(
            "Expected config shape: 'auto' infers from the live config; "
            "'interactive' expects built-in memory/user-profile enabled; "
            "'extraction' expects them disabled in this profile."
        ),
    )
    doctor.set_defaults(func=_handle_cli)

    repair_plan = sub.add_parser(
        "repair-plan",
        help="Report-only repair plan derived from doctor findings (no fixes applied)",
    )
    repair_plan.add_argument(
        "--hermes-home", default=_default_hermes_home(), help="Hermes home directory"
    )
    repair_plan.add_argument(
        "--expected-time",
        default=None,
        help="Expected wall-clock time (HH:MM) to compare against the live cron schedule",
    )
    repair_plan.add_argument(
        "--timezone",
        default=None,
        help="IANA timezone for --expected-time conversion (omit to skip schedule comparison)",
    )
    repair_plan.add_argument(
        "--profile-mode",
        choices=PROFILE_MODES_WITH_AUTO,
        default=PROFILE_MODE_AUTO,
        help=(
            "Expected config shape: 'auto' infers from the live config; "
            "'interactive' expects built-in memory/user-profile enabled; "
            "'extraction' expects them disabled in this profile."
        ),
    )
    repair_plan.set_defaults(func=_handle_cli)

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

    corpus_cleanup = sub.add_parser(
        "corpus-cleanup",
        help=(
            "Plan (default, dry-run) or --apply a duplicate/noise cleanup of "
            "an existing corpus; apply MOVES files to a backup, never deletes"
        ),
    )
    corpus_cleanup.add_argument(
        "--corpus-path",
        required=True,
        help="Corpus directory to clean (its turns/ subdirectory is scanned)",
    )
    corpus_cleanup.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Move the planned files into the backup directory and rebuild "
            "the dedup index (default is a read-only dry-run plan)"
        ),
    )
    corpus_cleanup.add_argument(
        "--backup-dir",
        default=None,
        help=(
            "Backup directory for moved files, MUST be outside the corpus "
            "tree (a recursive mine would re-ingest an in-corpus backup; "
            "default: <parent of corpus>/<corpus name>-cleanup-backup-"
            "<UTC stamp>/, a sibling of the corpus)"
        ),
    )
    corpus_cleanup.set_defaults(func=_handle_cli)

    integration = sub.add_parser(
        "integration-report",
        help="Report-only REM-style integration analysis (no writes)",
    )
    integration.add_argument(
        "--input-file",
        default=None,
        help="Path to a file with one memory text per line",
    )
    integration.add_argument(
        "--json-input",
        default=None,
        help='JSON array of memory texts (or {"text": ...} objects)',
    )
    integration.set_defaults(func=_handle_cli)


# ---------------------------------------------------------------------------
# Doctor helpers
# ---------------------------------------------------------------------------

_CRON_EXPR_RE = re.compile(r"(\d{1,2})\s+(\d{1,2})\s+(\*|\d+)\s+(\*|\d+)\s+(\*|\d+)")
_DREAMING_RE = re.compile(
    r"dream|sonho|mempalace-dreaming",
    re.IGNORECASE,
)


_CRON_BLOCK_HEADER_RE = re.compile(r"^\s*\S+\s+\[[^\]]+\]\s*$")
_CRON_BLOCK_NAME_RE = re.compile(r"^\s*name\s*[:=]\s*(.+?)\s*$", re.IGNORECASE)
_CRON_BLOCK_SCHED_RE = re.compile(r"^\s*schedule\s*[:=]\s*(.+?)\s*$", re.IGNORECASE)


def _is_dreaming_job(name: str) -> bool:
    """True when the job name looks dreaming-related."""
    return bool(_DREAMING_RE.search(name))


# --- Unsupported built-in short-memory cleanup cron (upstream #9763) -------
#
# The Hermes core cron scheduler creates agents with ``skip_memory=True``
# (cron/scheduler.py: "Cron system prompts would corrupt user
# representations"), so neither the built-in memory store nor the ``memory``
# tool is available in a cron session. A cron job that asks the agent to
# clean the built-in short memory (MEMORY.md / USER.md via
# ``memory(action=...)``) therefore silently does nothing while the job
# still reports "ok". Tracked upstream as NousResearch/hermes-agent#9763;
# this plugin only detects and reports it — it never patches Hermes core.

_SHORT_MEMORY_TARGET_RE = re.compile(
    r"mem[óo]ria\s+curta|short[\s-]?(?:term\s+)?memory|"
    r"\bMEMORY\.md\b|\bUSER\.md\b|memory\s*\(\s*action",
    re.IGNORECASE,
)
_SHORT_MEMORY_CLEANUP_RE = re.compile(
    r"limp|clean|remov|compact|apag|delet|prune",
    re.IGNORECASE,
)
#: Lines that *forbid* touching the built-in short memory (e.g. the dreaming
#: prompt's "Não altere MEMORY.md/USER.md ...") must not count as cleanup
#: intent. Matches an optional list marker ("7.", "-", "*") followed by a
#: negation in pt-BR or English.
_NEGATED_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s*|\d+[.)]\s*)?(?:n[ãa]o|never|don'?t|do\s+not)\b",
    re.IGNORECASE,
)

SHORT_MEMORY_CLEANUP_LIMITATION = (
    "Hermes cron sessions run with skip_memory=True, so the built-in "
    "memory store and the memory tool are unavailable in cron jobs; a "
    "scheduled built-in short-memory cleanup cannot work and will claim "
    "success without effect (upstream NousResearch/hermes-agent#9763)."
)


def _line_targets_short_memory_cleanup(line: str) -> bool:
    """True when one line carries both a built-in short-memory target and a
    cleanup intent, and is not a prohibition.

    Requiring same-line co-occurrence keeps a MemPalace dreaming prompt from
    being misread: there, "MEMORY.md/USER.md" appears only inside a
    prohibition line ("Não altere MEMORY.md/USER.md ...") while words like
    "compacto" live on unrelated lines — neither may count as scheduled
    built-in cleanup.
    """
    if _NEGATED_LINE_RE.match(line):
        return False
    return bool(
        _SHORT_MEMORY_TARGET_RE.search(line)
        and _SHORT_MEMORY_CLEANUP_RE.search(line)
    )


def _detect_short_memory_cleanup_jobs(jobs: list) -> list[dict[str, Any]]:
    """Return active agent cron jobs that target built-in short-memory
    cleanup (unsupported upstream, #9763).

    A job is flagged when it runs an agent (not a ``no_agent`` script), is
    active (not ``enabled: false`` nor ``state: "paused"`` — a paused job
    cannot fire, so it is not an active problem), and its name or one prompt
    line mentions both a built-in short-memory target (memória curta /
    MEMORY.md / USER.md / memory(action=...)) and a cleanup intent
    (limpar/clean/remove/compact/...) on the same line, outside a
    prohibition. Pure and tolerant: malformed entries are skipped, never
    raised on.
    """
    flagged: list[dict[str, Any]] = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        if job.get("no_agent"):
            continue
        if job.get("enabled") is False:
            continue
        if str(job.get("state", "")).strip().lower() == "paused":
            continue
        lines = [str(job.get("name", ""))]
        lines.extend(str(job.get("prompt", "")).splitlines())
        if any(_line_targets_short_memory_cleanup(line) for line in lines):
            flagged.append(
                {"id": str(job.get("id", "")), "name": str(job.get("name", ""))}
            )
    return flagged


def _read_cron_jobs_file(home: Path) -> tuple[list, str | None]:
    """Read ``<home>/cron/jobs.json`` read-only, never raising.

    Returns ``(jobs, None)`` on success, ``([], None)`` when the file does
    not exist (nothing scheduled — not an error), or ``([], reason)`` when
    it exists but cannot be read/parsed.
    """
    path = home / "cron" / "jobs.json"
    if not path.is_file():
        return ([], None)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ([], f"could not read cron jobs.json at {str(path)!r}: {exc}")
    if isinstance(data, list):
        return (data, None)
    if isinstance(data, dict) and isinstance(data.get("jobs"), list):
        return (data["jobs"], None)
    return ([], f"unexpected cron jobs.json shape at {str(path)!r}")


def _load_lean_check_job_name() -> str | None:
    """Resolve ``LEAN_CHECK_JOB_NAME`` without relying on package import state."""

    try:
        from mempalace_dreaming.setup import LEAN_CHECK_JOB_NAME

        return LEAN_CHECK_JOB_NAME
    except ImportError:
        pass

    import importlib.util
    import sys

    setup_path = PLUGIN_DIR / "mempalace_dreaming" / "setup.py"
    if not setup_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "mempalace_dreaming_setup", setup_path
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - doctor must never raise
        return None
    return getattr(module, "LEAN_CHECK_JOB_NAME", None)


def _parse_cron_blocks(stdout: str) -> list[dict]:
    """Parse the real multi-line ``hermes cron list`` block format.

    Each job is introduced by a header line like ``86ebf7425e3c [active]``
    followed by indented ``Key: value`` lines (``Name:``, ``Schedule:``,
    ``Repeat:``, ...). Returns ``[]`` when the input is not block-shaped so
    the caller can fall back to the table/key-value parser. Never raises.
    """
    lines = stdout.splitlines()
    if not any(_CRON_BLOCK_HEADER_RE.match(ln) for ln in lines):
        return []

    jobs: list[dict] = []
    cur: dict | None = None

    def _flush() -> None:
        if cur and cur.get("name"):
            jobs.append(cur)

    for line in lines:
        if _CRON_BLOCK_HEADER_RE.match(line):
            _flush()
            cur = {"name": "", "schedule": "", "raw": line.strip()}
            continue
        if cur is None:
            continue
        name_m = _CRON_BLOCK_NAME_RE.match(line)
        if name_m:
            cur["name"] = name_m.group(1).strip()
            continue
        sched_m = _CRON_BLOCK_SCHED_RE.match(line)
        if sched_m:
            value = sched_m.group(1).strip()
            cron_m = _CRON_EXPR_RE.search(value)
            cur["schedule"] = cron_m.group(0).strip() if cron_m else value
    _flush()
    return jobs


def _parse_cron_jobs(stdout: str) -> list[dict]:
    """Tolerant, never-raising parser for ``hermes cron list`` output.

    Accepts table-ish lines, ``name: ... schedule: ...`` key-value lines, and
    JSON arrays. Returns a list of dicts with at minimum ``name``, ``schedule``
    (a 5-field cron expression or ``""``), and ``raw`` (the original line or
    JSON item).
    """
    if not stdout or not stdout.strip():
        return []
    try:
        # Try JSON first
        data = json.loads(stdout)
        if isinstance(data, list):
            result: list[dict] = []
            for item in data:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    schedule = str(item.get("schedule", "")).strip()
                    result.append({"name": name, "schedule": schedule, "raw": str(item)})
            return result
    except (ValueError, TypeError):
        pass

    # Real `hermes cron list` block format, e.g.:
    #   86ebf7425e3c [active]
    #     Name:      mempalace-dreaming-daily
    #     Schedule:  30 08 * * *
    #     Repeat:    ∞
    block_jobs = _parse_cron_blocks(stdout)
    if block_jobs:
        return block_jobs

    jobs: list[dict] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Key-value format: "name: ... schedule: ..."
        kv_name_match = re.search(
            r"\bname\s*[:=]\s*([A-Za-z][A-Za-z0-9_.:\-\s]*?)(?=\s+schedule\b|$)",
            stripped,
        )
        if kv_name_match:
            name = kv_name_match.group(1).strip()
            cron_m = _CRON_EXPR_RE.search(stripped)
            schedule = cron_m.group(0).strip() if cron_m else ""
            jobs.append({"name": name, "schedule": schedule, "raw": stripped})
            continue

        # Table-style: look for a cron expression anywhere in the line
        cron_m = _CRON_EXPR_RE.search(stripped)
        if cron_m:
            schedule = cron_m.group(0).strip()
            before = stripped[: cron_m.start()].strip()
            # Tokenize the part before the cron expression, skipping:
            # - pure-digit tokens (ID column)
            # - all-uppercase short tokens <= 8 chars (header columns like ID/NAME/SCHEDULE)
            tokens = before.split()
            name_tokens: list[str] = []
            for tok in tokens:
                if tok.isdigit():
                    continue
                if tok.isupper() and len(tok) <= 8:
                    continue
                name_tokens.append(tok)
            if name_tokens:
                # Rebuild the name from the original text starting at the
                # first accepted token through to the cron expression.
                first_tok = name_tokens[0]
                idx = before.find(first_tok)
                candidate_name = before[idx:].strip()
                jobs.append({"name": candidate_name, "schedule": schedule, "raw": stripped})
    return jobs


def _lookup_dotted(data: object, dotted: str) -> tuple[bool, object]:
    """Navigate a nested mapping by a dotted key.

    Tolerates ``-``/``_`` differences in a segment name (the live config
    uses the dash-form plugin id ``mempalace-dreaming`` while doctor looks
    it up via ``plugins.mempalace_dreaming.*``). Returns ``(found, value)``.
    """
    node: object = data
    for seg in dotted.split("."):
        if not isinstance(node, dict):
            return (False, None)
        if seg in node:
            node = node[seg]
            continue
        for alt in (seg.replace("_", "-"), seg.replace("-", "_")):
            if alt in node:
                node = node[alt]
                break
        else:
            return (False, None)
    return (True, node)


def _read_hermes_config(path_str: str) -> tuple[dict | None, str | None]:
    """Read and parse the Hermes YAML config (read-only).

    Returns ``(data, None)`` on success or ``(None, reason)`` on any
    failure. Never raises.
    """
    text_path = (path_str or "").strip()
    if not text_path:
        return (None, "`hermes config path` returned empty output")
    try:
        import yaml  # lazy: pyyaml may be absent in some runtimes
    except ImportError:
        return (None, "pyyaml is not available to parse the hermes config")
    try:
        raw = Path(text_path).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        return (None, f"could not read hermes config at {text_path!r}: {exc}")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return (None, f"could not parse hermes config YAML: {exc}")
    if not isinstance(data, dict):
        return (None, f"hermes config at {text_path!r} is not a mapping")
    return (data, None)


def _cron_fields_match(a: str, b: str) -> bool:
    """Compare two 5-field cron expressions field-by-field.

    The first two fields (minute, hour) are compared as integers to handle
    zero-padding differences (e.g. ``8`` vs ``08``). The remaining three
    fields are compared as strings.
    """
    fa = a.split()
    fb = b.split()
    if len(fa) != 5 or len(fb) != 5:
        return False
    try:
        if int(fa[0]) != int(fb[0]):
            return False
        if int(fa[1]) != int(fb[1]):
            return False
    except ValueError:
        return False
    return fa[2:] == fb[2:]


def build_doctor_report(
    hermes_home: str | Path | None = None,
    *,
    run_fn: VerifyRunFn = _default_verify_run,
    expected_time: str | None = None,
    timezone: str | None = None,
    profile_mode: str = PROFILE_MODE_AUTO,
) -> dict[str, Any]:
    """Read-only operational audit of the MemPalace Dreaming installation.

    Checks plugin presence, memory provider, config coherence, and cron state.
    Never raises — every failure is captured into the returned JSON-serializable
    dict. Never writes to config, memory, cron, Obsidian, or the filesystem.

    Args:
        hermes_home: Hermes home directory (default ``~/.hermes``).
        run_fn: Injectable runner (default :func:`_default_verify_run`).
        expected_time: Optional ``"HH:MM"`` wall-clock time to compare against
            the live cron schedule (after UTC conversion via ``timezone``).
        timezone: IANA timezone for ``expected_time`` conversion. If ``None``
            and ``expected_time`` is given, no schedule comparison is made
            (schedule_mismatch stays None). Omitting ``expected_time`` entirely
            also keeps schedule_mismatch None.
    """
    hermes_home = hermes_home or _default_hermes_home()
    schedule_job_name = _load_schedule_job_name()
    lean_check_job_name = _load_lean_check_job_name()
    requested_profile_mode = _normalize_profile_mode(profile_mode, allow_auto=True)

    home = Path(hermes_home).expanduser()
    warnings: list[str] = []
    recommendations: list[str] = []

    def _safe_run(argv: list[str]) -> dict:
        try:
            return run_fn(argv)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "returncode": -1,
                "stdout": "",
                "stderr": "",
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # 1. Plugin presence
    # ------------------------------------------------------------------
    bundled_skill_exists = SKILL_PATH.is_file()
    engine_module_available = _module_importable("mempalace_dreaming.engine")
    setup_module_available = _module_importable("mempalace_dreaming.setup")

    if not bundled_skill_exists:
        warnings.append("bundled skill file is missing")
        recommendations.append("re-clone or reinstall the plugin to restore the skill file")
    if not engine_module_available:
        warnings.append("mempalace_dreaming.engine module is not available")
        recommendations.append("check that mempalace_dreaming/engine.py is present in the plugin directory")
    if not setup_module_available:
        warnings.append("mempalace_dreaming.setup module is not available")
        recommendations.append("check that mempalace_dreaming/setup.py is present in the plugin directory")

    # ------------------------------------------------------------------
    # 2. Memory / Hermes CLI
    # ------------------------------------------------------------------
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

    if not hermes_cli_callable:
        detail = version_res.get("error") or f"exit {version_res.get('returncode')}"
        warnings.append(f"hermes CLI is not callable: {detail}")
        recommendations.append("ensure hermes is installed and on PATH")
    if not memory_status_ok:
        detail = status_res.get("error") or f"exit {status_res.get('returncode')}"
        warnings.append(f"'hermes memory status' failed: {detail}")
        recommendations.append("check hermes memory configuration")
    elif not provider_is_mempalace:
        warnings.append(
            f"memory provider is not 'mempalace' (detected: {memory_provider!r})"
        )
        recommendations.append("set memory.provider=mempalace in hermes config")

    # ------------------------------------------------------------------
    # 3. Config coherence
    # ------------------------------------------------------------------
    config_checks: dict[str, Any] = {}
    config_coherent = True

    # `hermes config` has no `get` subcommand; resolve the YAML path and read
    # it directly (read-only). Any failure degrades into a JSON warning.
    path_res = _safe_run(["hermes", "config", "path"])
    config_data: dict | None = None
    config_error: str | None = None
    if not path_res.get("ok"):
        config_error = (
            path_res.get("error")
            or f"`hermes config path` failed (exit {path_res.get('returncode')})"
        )
    else:
        config_data, config_error = _read_hermes_config(path_res.get("stdout", ""))

    detected_profile_mode = _infer_profile_mode_from_config(config_data)
    resolved_profile_mode = (
        detected_profile_mode
        if requested_profile_mode == PROFILE_MODE_AUTO
        else requested_profile_mode
    )
    expected_config = _build_profile_config(resolved_profile_mode)
    expected_desc: dict[str, str] = {
        "memory.memory_enabled": "truthy"
        if resolved_profile_mode == PROFILE_MODE_INTERACTIVE
        else "falsy",
        "memory.user_profile_enabled": "truthy"
        if resolved_profile_mode == PROFILE_MODE_INTERACTIVE
        else "falsy",
        "memory.provider": '"mempalace"',
        "plugins.mempalace_dreaming.enabled": "truthy",
        "plugins.mempalace_dreaming.skill": '"mempalace-dreaming:mempalace-dreaming"',
    }

    if config_error is not None:
        config_coherent = False
        config_checks["config_error"] = config_error
        warnings.append(f"could not read hermes config: {config_error}")
        recommendations.append(
            "verify `hermes config path` resolves to a readable YAML file"
        )

    for key in expected_config:
        exp = expected_config[key]
        if config_data is None:
            found, parsed = False, None
        else:
            found, parsed = _lookup_dotted(config_data, key)
        if not found:
            ok_val = False
        elif isinstance(exp, bool):
            ok_val = bool(parsed) is exp
        else:
            ok_val = str(parsed).strip().lower() == str(exp).strip().lower()
        config_checks[key] = {
            "raw": "" if not found else str(parsed),
            "value": parsed if found else None,
            "ok": ok_val,
            "expected": expected_desc[key],
        }
        if not ok_val:
            config_coherent = False
            # Only emit a per-key warning when the config itself was readable;
            # an unreadable config already produced a single aggregate warning.
            if config_error is None:
                warnings.append(
                    f"config key '{key}' is not set correctly "
                    f"(expected {expected_desc[key]}, got {parsed!r})"
                )
                recommendations.append(f"run: hermes config set {key} {exp}")

    config_checks["config_coherent"] = config_coherent
    config_checks["requested_profile_mode"] = requested_profile_mode
    config_checks["detected_profile_mode"] = detected_profile_mode
    config_checks["resolved_profile_mode"] = resolved_profile_mode

    # ------------------------------------------------------------------
    # 4. Cron inspection
    # ------------------------------------------------------------------
    cron_res = _safe_run(["hermes", "cron", "list"])
    cron_jobs = _parse_cron_jobs(cron_res.get("stdout", ""))

    dreaming_jobs = [j for j in cron_jobs if _is_dreaming_job(j.get("name", ""))]
    duplicate_candidates = [
        j
        for j in dreaming_jobs
        if j.get("name") != lean_check_job_name
    ]
    daily_job_present = schedule_job_name is not None and any(
        j["name"] == schedule_job_name for j in dreaming_jobs
    )
    duplicate_dreaming_jobs = len(duplicate_candidates) > 1

    if schedule_job_name is None:
        warnings.append(
            "could not resolve the dreaming job name "
            "(mempalace_dreaming.setup unavailable); skipped cron presence check"
        )
        recommendations.append(
            "check that mempalace_dreaming/setup.py is present in the plugin directory"
        )
    elif not daily_job_present:
        warnings.append(
            f"daily dreaming job '{schedule_job_name}' is not present in cron list"
        )
        recommendations.append(
            f"run: hermes mempalace-dreaming setup --apply --schedule-dreaming --create-cron"
        )
    if duplicate_dreaming_jobs:
        dup_names = [j["name"] for j in duplicate_candidates]
        warnings.append(
            f"duplicate dreaming-like cron jobs detected: {dup_names!r}"
        )
        recommendations.append(
            "run `hermes cron list` and remove the duplicate/legacy job by id"
        )

    # Unsupported built-in short-memory cleanup cron (upstream #9763).
    # Read-only inspection of <home>/cron/jobs.json — the `hermes cron list`
    # output does not carry prompts, so the jobs file is the honest source.
    raw_jobs, jobs_file_error = _read_cron_jobs_file(home)
    short_memory_cleanup_jobs = _detect_short_memory_cleanup_jobs(raw_jobs)
    if jobs_file_error is not None:
        warnings.append(jobs_file_error)
        recommendations.append(
            "verify that $HERMES_HOME/cron/jobs.json is readable JSON"
        )
    if short_memory_cleanup_jobs:
        names = [j["name"] for j in short_memory_cleanup_jobs]
        warnings.append(
            f"cron job(s) {names!r} target built-in short-memory cleanup, "
            "which is unsupported upstream (#9763): cron sessions run with "
            "skip_memory=True, so the memory tool is unavailable and the "
            "job reports ok without cleaning anything"
        )
        recommendations.append(
            "pause or remove the short-memory cleanup cron (find its id via "
            "`hermes cron list`) and run the cleanup in an interactive "
            "(interativa) session instead, until "
            "NousResearch/hermes-agent#9763 is fixed upstream"
        )

    # ------------------------------------------------------------------
    # 5. Expected schedule comparison
    # ------------------------------------------------------------------
    schedule_mismatch: bool | None = None
    expected_cron_utc: str | None = None
    expected_schedule_error: str | None = None

    if expected_time is not None and timezone is not None:
        try:
            conv = convert_to_utc_cron(expected_time, timezone)
            expected_cron_utc = conv["cron_utc"]
            # Find the daily job and compare
            daily_job = next(
                (j for j in dreaming_jobs if j["name"] == schedule_job_name),
                None,
            )
            if daily_job is not None:
                job_schedule = daily_job.get("schedule", "")
                if _cron_fields_match(job_schedule, expected_cron_utc):
                    schedule_mismatch = False
                else:
                    schedule_mismatch = True
                    warnings.append(
                        f"schedule mismatch: daily job has '{job_schedule}' "
                        f"but expected '{expected_cron_utc}' "
                        f"(from {expected_time!r} in {timezone!r})"
                    )
                    recommendations.append(
                        f"update or recreate the cron job with schedule '{expected_cron_utc}'"
                    )
            # If daily job absent, schedule_mismatch stays None
        except ZoneInfoNotFoundError as exc:
            expected_schedule_error = f"unknown timezone {timezone!r}: {exc}"
            warnings.append(
                f"--timezone {timezone!r} is not a valid IANA timezone: {exc}"
            )
            # schedule_mismatch stays None, expected_cron_utc stays None

    cron_check: dict[str, Any] = {
        "daily_job_present": daily_job_present,
        "dreaming_jobs": dreaming_jobs,
        "daily_duplicate_candidates": duplicate_candidates,
        "duplicate_dreaming_jobs": duplicate_dreaming_jobs,
        "short_memory_cleanup_jobs": short_memory_cleanup_jobs,
        "short_memory_cleanup_unsupported": bool(short_memory_cleanup_jobs),
        "schedule_mismatch": schedule_mismatch,
    }
    if expected_cron_utc is not None:
        cron_check["expected_cron_utc"] = expected_cron_utc
    elif expected_time is not None and timezone is not None:
        # Timezone error branch
        cron_check["expected_cron_utc"] = None
    if expected_schedule_error is not None:
        cron_check["expected_schedule_error"] = expected_schedule_error

    # ------------------------------------------------------------------
    # ok rollup
    # ------------------------------------------------------------------
    ok = (
        hermes_cli_callable
        and memory_status_ok
        and provider_is_mempalace
        and bundled_skill_exists
        and engine_module_available
        and setup_module_available
        and config_coherent
        and daily_job_present
        and not duplicate_dreaming_jobs
        and not short_memory_cleanup_jobs
        and schedule_mismatch is not True
    )

    return {
        "plugin": PLUGIN_NAME,
        "version": PLUGIN_VERSION,
        "hermes_home": str(home),
        "ok": ok,
        "warnings": warnings,
        "recommendations": recommendations,
        "checks": {
            "bundled_skill_exists": bundled_skill_exists,
            "engine_module_available": engine_module_available,
            "setup_module_available": setup_module_available,
            "plugin_status": PLUGIN_STATUS,
            "hermes_cli_callable": hermes_cli_callable,
            "memory_status_ok": memory_status_ok,
            "memory_provider": memory_provider,
            "provider_is_mempalace": provider_is_mempalace,
            "profile_mode": resolved_profile_mode,
            "config": config_checks,
            "cron": cron_check,
        },
    }


_REPAIR_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _config_expected_literal(expected_desc: str) -> str:
    """Turn a doctor ``expected`` description into a config-set literal.

    doctor stores expectations as human strings (``truthy``,
    ``"mempalace"``, ``"mempalace-dreaming:mempalace-dreaming"``). This derives the
    value to show in a ``hermes config set`` *preview* (never executed),
    so repair-plan stays coherent with doctor without duplicating its map.
    """
    desc = (expected_desc or "").strip()
    if desc == "truthy":
        return "true"
    if len(desc) >= 2 and desc[0] == '"' and desc[-1] == '"':
        return desc[1:-1]
    return desc


def build_repair_plan(
    hermes_home: str | Path | None = None,
    *,
    run_fn: VerifyRunFn = _default_verify_run,
    expected_time: str | None = None,
    timezone: str | None = None,
    profile_mode: str = PROFILE_MODE_AUTO,
) -> dict[str, Any]:
    """Turn ``doctor`` findings into an explicit, report-only repair plan.

    Reuses :func:`build_doctor_report` for detection, then maps each failed
    check into an ordered ``repairs`` list. Strictly report-only: it never
    writes config/cron/memory/files and never executes any command — every
    ``command_preview`` is a string the operator may choose to run, not an
    action taken here. Never raises: any failure is captured into the
    JSON-serializable result via the underlying doctor report.
    """
    hermes_home = hermes_home or _default_hermes_home()

    doctor = build_doctor_report(
        hermes_home=hermes_home,
        run_fn=run_fn,
        expected_time=expected_time,
        timezone=timezone,
        profile_mode=profile_mode,
    )
    checks = doctor.get("checks", {})
    doctor_profile_mode = checks.get("profile_mode", PROFILE_MODE_INTERACTIVE)
    repairs: list[dict[str, Any]] = []

    def add(
        repair_id: str,
        priority: str,
        kind: str,
        reason: str,
        suggested_action: str,
        command_preview: str | None = None,
    ) -> None:
        repairs.append(
            {
                "id": repair_id,
                "priority": priority,
                "kind": kind,
                "reason": reason,
                "suggested_action": suggested_action,
                "command_preview": command_preview,
            }
        )

    # --- Plugin presence -------------------------------------------------
    if checks.get("bundled_skill_exists") is False:
        add(
            "restore-bundled-skill",
            "high",
            "plugin",
            "the bundled skill file is missing from the plugin directory",
            "re-clone or reinstall the plugin to restore the skill file",
        )
    if checks.get("engine_module_available") is False:
        add(
            "restore-engine-module",
            "high",
            "plugin",
            "mempalace_dreaming.engine is not importable",
            "ensure mempalace_dreaming/engine.py is present in the plugin directory",
        )
    if checks.get("setup_module_available") is False:
        add(
            "restore-setup-module",
            "high",
            "plugin",
            "mempalace_dreaming.setup is not importable",
            "ensure mempalace_dreaming/setup.py is present in the plugin directory",
        )

    # --- Environment / Hermes CLI ---------------------------------------
    if checks.get("hermes_cli_callable") is False:
        add(
            "install-hermes-cli",
            "high",
            "environment",
            "the 'hermes' CLI is not callable",
            "verify the hermes installation and that it is on the PATH; "
            "no automatic fix is attempted",
        )
    if checks.get("memory_status_ok") is False:
        add(
            "fix-memory-status",
            "high",
            "environment",
            "'hermes memory status' did not succeed",
            "inspect the hermes memory configuration manually; "
            "no automatic fix is attempted",
        )

    # --- Memory provider (runtime) --------------------------------------
    if (
        checks.get("memory_status_ok") is True
        and checks.get("provider_is_mempalace") is False
    ):
        detected = checks.get("memory_provider")
        add(
            "set-memory-provider",
            "high",
            "config",
            f"the active memory provider is {detected!r}, not 'mempalace'",
            "set the memory provider to mempalace, then restart the hermes runtime",
            "hermes config set memory.provider mempalace",
        )

    # --- Config coherence -----------------------------------------------
    config = checks.get("config", {})
    config_error = config.get("config_error")
    if config_error is not None:
        add(
            "fix-config-readability",
            "high",
            "config",
            f"the hermes config could not be read: {config_error}",
            "verify that `hermes config path` resolves to a readable YAML "
            "file; no `hermes config set` is suggested until it is readable",
            None,
        )
    else:
        for key, entry in config.items():
            if not isinstance(entry, dict) or "ok" not in entry:
                continue
            if entry.get("ok") is True:
                continue
            if key == "memory.provider" and any(
                r["id"] == "set-memory-provider" for r in repairs
            ):
                # Already covered by the provider repair; avoid a duplicate
                # `hermes config set memory.provider` preview.
                continue
            literal = _config_expected_literal(str(entry.get("expected", "")))
            add(
                f"set-config-{key}",
                "high",
                "config",
                f"config key '{key}' is not coherent "
                f"(expected {entry.get('expected')!r}, got {entry.get('value')!r})",
                f"set config key '{key}' to the expected value",
                f"hermes config set {key} {literal}",
            )

    # --- Cron state ------------------------------------------------------
    cron = checks.get("cron", {})
    if cron.get("daily_job_present") is False and checks.get(
        "setup_module_available"
    ) is not False:
        add(
            "create-daily-cron",
            "medium",
            "cron",
            "the daily dreaming cron job is not present",
            "create the daily dreaming cron via the documented setup flow",
            "hermes mempalace-dreaming setup --apply --schedule-dreaming "
            f"--create-cron --profile-mode {doctor_profile_mode}",
        )
    if cron.get("duplicate_dreaming_jobs") is True:
        dup_names = [j.get("name", "") for j in cron.get("dreaming_jobs", [])]
        add(
            "remove-duplicate-cron",
            "medium",
            "cron",
            f"multiple dreaming-like cron jobs detected: {dup_names!r}",
            "run `hermes cron list`, identify the duplicate/legacy job by "
            "its id, then remove it manually — the id is never inferred or "
            "invented here",
            "hermes cron list",
        )
    if cron.get("short_memory_cleanup_jobs"):
        names = [j.get("name", "") for j in cron["short_memory_cleanup_jobs"]]
        add(
            "unsupported-short-memory-cleanup-cron",
            "medium",
            "cron",
            f"cron job(s) {names!r} target built-in short-memory cleanup, "
            "which cannot work in cron sessions (upstream "
            "NousResearch/hermes-agent#9763: cron runs with "
            "skip_memory=True, so the memory tool is unavailable); the job "
            "reports ok without cleaning anything",
            "pause or remove the job manually (its id comes from "
            "`hermes cron list`, never inferred here) and run the built-in "
            "short-memory cleanup in an interactive session; do not patch "
            "Hermes core — the defect is tracked upstream as #9763",
            "hermes cron list",
        )
    if cron.get("schedule_mismatch") is True:
        job_sched = None
        for j in cron.get("dreaming_jobs", []):
            if j.get("schedule"):
                job_sched = j.get("schedule")
                break
        expected_cron = cron.get("expected_cron_utc")
        preview = None
        if expected_time is not None and timezone is not None:
            preview = (
                "hermes mempalace-dreaming setup --apply --schedule-dreaming "
                f"--create-cron --time {expected_time} --timezone {timezone} "
                f"--profile-mode {doctor_profile_mode}"
            )
        add(
            "align-cron-schedule",
            "medium",
            "cron",
            f"the daily cron schedule {job_sched!r} differs from the "
            f"expected {expected_cron!r}",
            "remove the stale daily job (see remove-duplicate-cron guidance "
            "for the id) and recreate it with the corrected schedule via the "
            "documented setup flow; nothing is auto-applied",
            preview,
        )

    repairs.sort(key=lambda r: _REPAIR_PRIORITY_RANK.get(r["priority"], 99))

    ok = bool(doctor.get("ok"))
    if ok or not repairs:
        summary = "No repairs needed; doctor reports the installation is healthy."
    else:
        summary = f"{len(repairs)} repair action(s) suggested, ordered by priority."

    return {
        "plugin": PLUGIN_NAME,
        "version": PLUGIN_VERSION,
        "hermes_home": doctor.get("hermes_home", str(Path(hermes_home).expanduser())),
        "ok": ok,
        "summary": summary,
        "warnings": doctor.get("warnings", []),
        "repairs": repairs,
    }


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


def _default_provider_copy(source: str, target: str) -> None:
    """Copy a bundled provider artifact into the Hermes plugins directory."""
    target_path = Path(target).expanduser()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(source).expanduser(), target_path)


def _default_provider_install(argv) -> None:
    """Install the mempalace CLI via an argv list (never a shell string)."""
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


def _load_schedule_job_name() -> str | None:
    """Resolve ``SCHEDULE_JOB_NAME`` without depending on ``sys.path``.

    Mirrors :func:`_load_apply_setup_plan`: normal package import first, then
    a direct load of ``mempalace_dreaming/setup.py`` from :data:`PLUGIN_DIR`
    when the plugin file is loaded standalone (the installed-plugin case,
    where ``mempalace_dreaming`` is not guaranteed to be importable by name).

    Unlike :func:`_load_apply_setup_plan`, this never raises: it returns
    ``None`` if the setup module is genuinely unavailable, so
    :func:`build_doctor_report` can degrade into a JSON warning instead of
    crashing with a traceback.
    """
    try:
        from mempalace_dreaming.setup import SCHEDULE_JOB_NAME

        return SCHEDULE_JOB_NAME
    except ImportError:
        pass

    import importlib.util
    import sys

    setup_path = PLUGIN_DIR / "mempalace_dreaming" / "setup.py"
    if not setup_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "mempalace_dreaming_setup", setup_path
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses can resolve annotations.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - doctor must never raise
        return None
    return getattr(module, "SCHEDULE_JOB_NAME", None)


def _apply_setup_from_args(
    args,
    *,
    mkdir_fn=_default_mkdir,
    run_fn=_default_run,
    schedule_fn=_default_schedule,
    lean_check_schedule_fn=_default_schedule,
    provider_copy_fn=_default_provider_copy,
    provider_install_fn=_default_provider_install,
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
        install_provider=getattr(args, "install_provider", False),
        install_method=getattr(args, "install_method", "auto"),
        schedule_lean_check=getattr(args, "schedule_lean_check", False),
        lean_check_time=getattr(args, "lean_check_time", "06:30"),
        lean_check_weekday=getattr(
            args, "lean_check_weekday", _LEAN_CHECK_DEFAULT_WEEKDAY
        ),
        profile_mode=getattr(args, "profile_mode", PROFILE_MODE_INTERACTIVE),
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
        lean_check_schedule_fn=lean_check_schedule_fn,
        create_lean_check_cron=getattr(
            args, "create_lean_check_cron", False
        ),
        provider_copy_fn=provider_copy_fn,
        provider_install_fn=provider_install_fn,
        install_provider=getattr(args, "install_provider", False),
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


def _load_build_integration_report():
    """Resolve ``build_integration_report`` without depending on ``sys.path``.

    Mirrors :func:`_load_build_lean_check_report`.
    """
    try:
        from mempalace_dreaming.engine import build_integration_report

        return build_integration_report
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
        return module.build_integration_report


def _load_cleanup_module():
    """Resolve ``mempalace_dreaming.cleanup`` without depending on ``sys.path``.

    Mirrors :func:`_load_build_lean_check_report`.
    """
    try:
        from mempalace_dreaming import cleanup

        return cleanup
    except ImportError:
        import importlib.util
        import sys

        cleanup_path = PLUGIN_DIR / "mempalace_dreaming" / "cleanup.py"
        spec = importlib.util.spec_from_file_location(
            "mempalace_dreaming_cleanup", cleanup_path
        )
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module


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
    if cmd == "doctor":
        report = build_doctor_report(
            hermes_home=getattr(args, "hermes_home", "~/.hermes"),
            expected_time=getattr(args, "expected_time", None),
            timezone=getattr(args, "timezone", None),
            profile_mode=getattr(args, "profile_mode", PROFILE_MODE_AUTO),
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    if cmd == "repair-plan":
        report = build_repair_plan(
            hermes_home=getattr(args, "hermes_home", "~/.hermes"),
            expected_time=getattr(args, "expected_time", None),
            timezone=getattr(args, "timezone", None),
            profile_mode=getattr(args, "profile_mode", PROFILE_MODE_AUTO),
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    if cmd == "lean-check":
        build_lean_check_report = _load_build_lean_check_report()
        candidates, input_warnings = _read_lean_check_inputs(args)
        report = build_lean_check_report(
            candidates, extra_warnings=input_warnings
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    if cmd == "corpus-cleanup":
        cleanup = _load_cleanup_module()
        plan = cleanup.build_corpus_cleanup_plan(
            getattr(args, "corpus_path", "")
        )
        result = cleanup.apply_corpus_cleanup(
            plan,
            apply=getattr(args, "apply", False),
            backup_dir=getattr(args, "backup_dir", None),
        )
        print(json.dumps(
            {"plan": plan, "result": result}, indent=2, ensure_ascii=False
        ))
        return
    if cmd == "integration-report":
        build_integration_report = _load_build_integration_report()
        candidates, input_warnings = _read_lean_check_inputs(args)
        report = build_integration_report(candidates)
        report["warnings"] = list(input_warnings) + report["warnings"]
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
            install_provider=getattr(args, "install_provider", False),
            install_method=getattr(args, "install_method", "auto"),
            schedule_lean_check=getattr(args, "schedule_lean_check", False),
            lean_check_time=getattr(args, "lean_check_time", "06:30"),
            lean_check_weekday=getattr(
                args, "lean_check_weekday", _LEAN_CHECK_DEFAULT_WEEKDAY
            ),
            profile_mode=getattr(args, "profile_mode", PROFILE_MODE_INTERACTIVE),
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
