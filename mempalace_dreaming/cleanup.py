"""Corpus cleanup/migration tooling for an already-polluted MemPalace corpus.

The bundled provider now refuses to file cron/background sessions,
low-value maintenance reports, and duplicate content (see
``provider_bundle/provider_init.py``). This module migrates the *existing*
corpus that was polluted before that fix:

* :func:`build_corpus_cleanup_plan` — read-only scan of ``<corpus>/turns/``
  classifying each transcript as keep or remove (with a reason). It never
  mutates anything; unparseable files are conservatively kept.
* :func:`apply_corpus_cleanup` — dry-run by default. With ``apply=True`` it
  MOVES the planned files into a backup directory (never deletes) and
  rebuilds the dedup marker index for the kept files so future syncs
  dedupe against the cleaned corpus.

The palace itself (the mined HNSW store) is never read or written here.
After an applied cleanup the operator should re-mine the corpus so the
palace reflects the cleaned turns; that step is intentionally manual.

The classification patterns mirror the ingestion-side guards in
``provider_bundle/provider_init.py``. They are duplicated on purpose: the
provider bundle is copied standalone into ``$HERMES_HOME/plugins/`` and
cannot import this package, and this package must not import the bundle
(it imports the Hermes runtime).
"""
from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

MoveFn = Callable[[str, str], object]

#: Platforms recorded in a transcript header that mark a background session.
_BACKGROUND_PLATFORMS = frozenset({"cron"})

#: Session-id prefixes that mark a background session.
_BACKGROUND_SESSION_PREFIXES = ("cron_",)

# The cron runner's delivery wrapper, embedded verbatim in the filed user
# content of every cron turn.
_CRON_WRAPPER_MARKERS = (
    re.compile(r"you are running as a scheduled cron job", re.IGNORECASE),
    re.compile(r"respond with exactly \"?\[SILENT\]\"?", re.IGNORECASE),
)

# Low-value assistant replies (kept in sync with provider_init).
_LOW_VALUE_ASSISTANT_PATTERNS = (
    re.compile(r"^\[SILENT\]$", re.IGNORECASE),
    re.compile(r"^sem\s+novos\s+fatos\s+dur[áa]veis\b[^\n]{0,60}$", re.IGNORECASE),
    re.compile(
        r"^sem\s+limpeza\s+segura\s+na\s+mem[óo]ria\s+curta\b[^\n]{0,60}$",
        re.IGNORECASE,
    ),
    re.compile(r"^no\s+new\s+durable\s+facts\b[^\n]{0,60}$", re.IGNORECASE),
    re.compile(r"^#\s*MemPalace Dream Report\b", re.IGNORECASE),
    re.compile(r"^-\s*mem[óo]rias\s+salvas\s*:", re.IGNORECASE),
)

_CLEANUP_REPORT_LINE = re.compile(
    r"^-\s*(removi|traduzi|compactei|eliminei|mantive|deixei)\b",
    re.IGNORECASE,
)

_WS_RE = re.compile(r"\s+")

_HEADER_FIELD_RE = re.compile(r"(?m)^-\s*(session_id|platform)\s*:\s*(.+?)\s*$")


def _normalize_turn_text(user: str, assistant: str) -> str:
    """Same canonicalization the provider uses for its dedup index."""
    return _WS_RE.sub(" ", f"{user}\n{assistant}").casefold().strip()


def _normalized_digest(user: str, assistant: str) -> str:
    normalized = _normalize_turn_text(user, assistant)
    return hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()[:24]


def _is_low_value_reply(assistant: str) -> bool:
    text = (assistant or "").strip()
    if not text:
        return False
    if any(p.search(text) for p in _LOW_VALUE_ASSISTANT_PATTERNS):
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and all(_CLEANUP_REPORT_LINE.match(ln) for ln in lines):
        return True
    return False


def _parse_turn_file(text: str) -> dict | None:
    """Parse the deterministic transcript format written by the provider.

    Returns ``{"session_id", "platform", "user", "assistant"}`` or ``None``
    when the file does not look like a Hermes turn transcript.
    """
    if "## User" not in text or "## Assistant" not in text:
        return None
    head, _, rest = text.partition("## User")
    user, _, assistant = rest.partition("## Assistant")
    fields = {m.group(1): m.group(2) for m in _HEADER_FIELD_RE.finditer(head)}
    return {
        "session_id": fields.get("session_id", ""),
        "platform": fields.get("platform", "").strip().lower(),
        "user": user.strip(),
        "assistant": assistant.strip(),
    }


def _classify(parsed: dict) -> str | None:
    """Removal reason for a parsed turn, or ``None`` to keep it.

    Duplicate detection is handled by the caller (it needs cross-file
    state); this only covers per-file signals.
    """
    if parsed["platform"] in _BACKGROUND_PLATFORMS:
        return "background-session"
    if any(
        parsed["session_id"].startswith(p) for p in _BACKGROUND_SESSION_PREFIXES
    ):
        return "background-session"
    if any(p.search(parsed["user"]) for p in _CRON_WRAPPER_MARKERS):
        return "background-session"
    if _is_low_value_reply(parsed["assistant"]):
        return "low-value"
    return None


def build_corpus_cleanup_plan(corpus_path: str | Path) -> dict[str, Any]:
    """Read-only cleanup plan for ``<corpus>/turns/``.

    Scans every ``turn-*.md`` (sorted by filename, i.e. chronologically —
    the provider embeds a UTC timestamp in the name) and classifies it:

    * ``background-session`` — cron platform, ``cron_*`` session id, or the
      cron delivery wrapper in the user content;
    * ``low-value`` — maintenance/report replies with no durable value;
    * ``duplicate-content`` — normalized content already seen in an earlier
      *kept* file (the earliest copy is always kept).

    Unparseable files are kept and reported in ``warnings``. Nothing is
    mutated; the result is JSON-serializable and deterministic.
    """
    corpus = Path(corpus_path).expanduser()
    turns_dir = corpus / "turns"
    plan: dict[str, Any] = {
        "corpus": str(corpus),
        "report_only": True,
        "scanned": 0,
        "keep": [],
        "remove": [],
        "counts": {
            "background-session": 0,
            "low-value": 0,
            "duplicate-content": 0,
            "kept": 0,
        },
        "warnings": [],
        "notes": [
            "Dry-run plan: nothing was moved or deleted.",
            "Apply moves files into a backup directory; it never deletes.",
            "The palace is never modified; re-mine the corpus after an "
            "applied cleanup so the palace reflects the cleaned turns.",
        ],
        "safety": {
            "report_only": True,
            "no_palace_writes": True,
            "no_deletes": True,
        },
    }

    if not turns_dir.is_dir():
        plan["warnings"].append(
            f"turns directory not found at {str(turns_dir)!r}; nothing to scan"
        )
        return plan

    seen_digests: set[str] = set()
    for path in sorted(turns_dir.glob("turn-*.md")):
        plan["scanned"] += 1
        try:
            parsed = _parse_turn_file(path.read_text(encoding="utf-8"))
        except OSError as exc:
            parsed = None
            plan["warnings"].append(f"could not read {path.name}: {exc}")
        if parsed is None:
            if not plan["warnings"] or path.name not in plan["warnings"][-1]:
                plan["warnings"].append(
                    f"unparseable turn file kept as-is: {path.name}"
                )
            plan["keep"].append(str(path))
            plan["counts"]["kept"] += 1
            continue

        reason = _classify(parsed)
        digest = _normalized_digest(parsed["user"], parsed["assistant"])
        if reason is None:
            if digest in seen_digests:
                reason = "duplicate-content"
            else:
                seen_digests.add(digest)

        if reason is None:
            plan["keep"].append(str(path))
            plan["counts"]["kept"] += 1
        else:
            plan["remove"].append(
                {"file": str(path), "reason": reason, "digest": digest}
            )
            plan["counts"][reason] += 1

    return plan


def apply_corpus_cleanup(
    plan: dict[str, Any],
    *,
    apply: bool = False,
    backup_dir: str | Path | None = None,
    move_fn: MoveFn | None = None,
) -> dict[str, Any]:
    """Apply (or just describe) a cleanup plan.

    With ``apply=False`` (default) nothing is touched and the result only
    restates what *would* happen. With ``apply=True`` every planned file is
    MOVED (never deleted) into ``<backup_dir>/turns/`` — default
    ``<corpus>/cleanup-backup-<UTC stamp>/`` — and the provider-compatible
    dedup marker index under ``<corpus>/turns/.dedup-index/`` is rebuilt
    from the kept files. Failures are captured in ``errors``; the palace is
    never read or written.
    """
    corpus = Path(plan.get("corpus", ""))
    result: dict[str, Any] = {
        "applied": False,
        "backup_dir": None,
        "planned_moves": len(plan.get("remove", [])),
        "moved": [],
        "index_markers_cleared": 0,
        "index_markers_created": 0,
        "errors": [],
        "notes": list(plan.get("notes", [])),
    }

    if not apply:
        return result

    if backup_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = corpus / f"cleanup-backup-{stamp}"
    backup = Path(backup_dir).expanduser()
    backup_turns = backup / "turns"
    result["backup_dir"] = str(backup)

    mover: MoveFn = move_fn or (lambda src, dst: shutil.move(src, dst))

    try:
        backup_turns.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result["errors"].append(f"could not create backup dir: {exc}")
        return result

    result["applied"] = True
    for entry in plan.get("remove", []):
        src = Path(entry["file"])
        dst = backup_turns / src.name
        try:
            mover(str(src), str(dst))
        except Exception as exc:  # noqa: BLE001 - collect, keep going
            result["errors"].append(f"could not move {src.name}: {exc}")
            continue
        result["moved"].append({"file": str(src), "to": str(dst),
                                "reason": entry.get("reason", "")})

    # Rebuild the dedup index from the kept files so the provider's
    # cross-session dedup starts from the cleaned corpus.
    index_dir = corpus / "turns" / ".dedup-index"
    try:
        index_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result["errors"].append(f"could not create dedup index dir: {exc}")
        return result

    # Rebuild means rebuild: stale markers (e.g. for content just moved to
    # the backup) would keep blocking that content from ever being filed
    # again, so the index is cleared before re-deriving it from the kept
    # files. Markers are derived cache, not corpus content.
    try:
        for stale in index_dir.iterdir():
            if not stale.is_file():
                continue
            stale.unlink()
            result["index_markers_cleared"] += 1
    except OSError as exc:
        result["errors"].append(f"could not clear stale dedup markers: {exc}")

    for kept in plan.get("keep", []):
        path = Path(kept)
        try:
            parsed = _parse_turn_file(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if parsed is None:
            continue
        digest = _normalized_digest(parsed["user"], parsed["assistant"])
        marker = index_dir / digest
        if marker.exists():
            continue
        try:
            marker.write_text(
                f"rebuilt-from {path.name}\n", encoding="utf-8"
            )
            result["index_markers_created"] += 1
        except OSError as exc:
            result["errors"].append(
                f"could not write dedup marker for {path.name}: {exc}"
            )

    return result
