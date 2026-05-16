"""Pure dreaming engine MVP.

Pipeline: mine -> score -> filter (durable) -> dedupe -> remember.

No Hermes tools are imported here. ``search_fn`` and ``remember_fn`` are
injected by the caller so the engine can be tested in isolation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

SearchFn = Callable[[str], Sequence[object]]
RememberFn = Callable[[str], object]

# Temporary / progress-like noise we never want as a durable memory.
_TEMPORARY_PATTERNS = (
    re.compile(r"\b(?:pr|issue|pull request)\s*#?\d+", re.IGNORECASE),
    re.compile(r"#\d+\b"),
    re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE),  # commit SHA
    re.compile(r"\b\d+\s+commits?\s+(?:ahead|behind)\b", re.IGNORECASE),
    re.compile(r"\bbranch\s+is\b", re.IGNORECASE),
    re.compile(r"\b(?:fixed|done|completed|complete)\b", re.IGNORECASE),
    re.compile(r"\bphase\s*\d+", re.IGNORECASE),
    # raw log-like lines, e.g. "2024-01-01 12:00:00 INFO ..."
    re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}"),
    re.compile(r"\b(?:INFO|DEBUG|WARN|WARNING|ERROR|TRACE)\b\s"),
)

# Secret-like content we must never persist.
_SECRET_PATTERNS = (
    re.compile(r"\bapi[\s_-]?key\b", re.IGNORECASE),
    re.compile(r"\btoken\b", re.IGNORECASE),
    re.compile(r"\bpassword\b", re.IGNORECASE),
    re.compile(r"\bsecret\b", re.IGNORECASE),
    re.compile(r"private key", re.IGNORECASE),
    re.compile(r"\.env\b", re.IGNORECASE),
    re.compile(r"connection string", re.IGNORECASE),
    re.compile(r"\b[A-Z][A-Z0-9_]*_(?:KEY|TOKEN|SECRET|PASSWORD)\b"),
    re.compile(r"\b(?:sk|ghp|xox[baprs])[-_]?[A-Za-z0-9]{4,}", re.IGNORECASE),
    re.compile(r"\b\w+://[^\s:]+:[^\s@]+@"),  # creds embedded in a URL
)

# Signals that an entry encodes a durable preference/environment/workflow fact.
_DURABLE_PATTERNS = (
    re.compile(r"\b(?:prefer|prefers|preferred|preference)\b", re.IGNORECASE),
    re.compile(r"\b(?:always|never|usually|by default|convention)\b", re.IGNORECASE),
    re.compile(r"\b(?:uses?|using|runs? on|environment|workflow|setup)\b", re.IGNORECASE),
    re.compile(r"\b(?:python|node|linux|macos|windows|docker)\b", re.IGNORECASE),
)

_MIN_DURABLE_SCORE = 1.0

# Remembered text must be concise. Anything longer is truncated with an
# ellipsis so the final string never exceeds this many characters.
_MAX_REMEMBERED_CHARS = 280
_ELLIPSIS = "..."

_WHITESPACE_RE = re.compile(r"\s+")
_BULLET_RE = re.compile(r"^[-*•]\s+")
# A leading conversational role label, optionally behind a bullet, e.g.
# "User:", "- Assistant:", "• System:". Requires the trailing colon so we
# only strip obvious labels, never sentences that merely begin with a role
# word ("Users prefer tabs").
_ROLE_LABEL_RE = re.compile(
    r"^(?:[-*•]\s+)?(?:user|assistant|system|human|ai)\s*:\s*",
    re.IGNORECASE,
)


def normalize_remembered_text(text: str) -> str:
    """Return a concise, normalized form of ``text`` for durable storage.

    Pure helper:

    * collapse any run of whitespace/newlines to a single space and trim;
    * strip an obvious leading bullet and/or role label (``User:`` /
      ``Assistant:`` ...), repeatedly, so ``- User: foo`` becomes ``foo``;
    * cap the result at ``_MAX_REMEMBERED_CHARS`` characters, appending
      ``...`` (counted within the cap) when truncation occurs.
    """
    collapsed = _WHITESPACE_RE.sub(" ", text).strip()

    while True:
        stripped = _ROLE_LABEL_RE.sub("", collapsed, count=1)
        stripped = _BULLET_RE.sub("", stripped, count=1).strip()
        if stripped == collapsed:
            break
        collapsed = stripped

    if len(collapsed) > _MAX_REMEMBERED_CHARS:
        head = collapsed[: _MAX_REMEMBERED_CHARS - len(_ELLIPSIS)].rstrip()
        collapsed = head + _ELLIPSIS
    return collapsed


@dataclass
class CandidateMemory:
    """A single mined fact under consideration for durable storage."""

    text: str
    kind: str = "fact"
    score: float = 0.0
    rejected: bool = False
    reason: str = ""


@dataclass
class DreamReport:
    """Outcome of a dream run."""

    remembered: int = 0
    duplicates: int = 0
    rejected: int = 0
    remembered_texts: list[str] = field(default_factory=list)


@dataclass
class RetrievalAuditReport:
    """Classification of retrieval results into useful vs noisy.

    Pure data: produced by :func:`audit_retrieval_noise`, which never reads
    or writes memory.
    """

    useful: list[str] = field(default_factory=list)
    noisy: list[str] = field(default_factory=list)
    total: int = 0
    useful_count: int = 0
    noisy_count: int = 0


def _matches_any(patterns: Iterable[re.Pattern[str]], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def _result_text(result: object) -> str:
    """Extract the text of a search result (str, mapping, or object)."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        return str(result.get("text", "")).strip()
    return str(getattr(result, "text", result)).strip()


def audit_retrieval_noise(
    search_results: Iterable[object],
) -> RetrievalAuditReport:
    """Classify retrieval results into useful vs noisy. Pure helper.

    A result is *noisy* when its text matches the same temporary/progress or
    secret-like patterns the dreaming pipeline rejects; otherwise it is
    *useful*. Empty/blank results are skipped (not counted). This function
    never writes memory and has no side effects.

    Accepts strings, mappings with a ``text`` key, or objects exposing a
    ``text`` attribute.
    """
    report = RetrievalAuditReport()
    for result in search_results:
        text = _result_text(result)
        if not text:
            continue
        report.total += 1
        if _matches_any(_TEMPORARY_PATTERNS, text) or _matches_any(
            _SECRET_PATTERNS, text
        ):
            report.noisy.append(text)
        else:
            report.useful.append(text)
    report.useful_count = len(report.useful)
    report.noisy_count = len(report.noisy)
    return report


# --- lean-check (report-only, no writes of any kind) ----------------------

# A clean candidate is "noisy" enough to recommend tightening recall when its
# share of the input exceeds this. A duplicate share above the second
# threshold suggests memory is accumulating near-dupes.
_LEAN_NOISE_RATE = 0.5
_LEAN_DUPLICATE_RATE = 0.3
# Examples are capped so the report stays small and never dumps a corpus.
_LEAN_EXAMPLE_CAP = 5
# Secret-like material is classified but never echoed back into the report.
_LEAN_SECRET_PLACEHOLDER = "[redacted: secret-like content]"


def build_lean_check_report(
    candidates: Iterable[object],
    *,
    search_fn: SearchFn | None = None,
    extra_warnings: Sequence[str] = (),
) -> dict:
    """Classify candidate memory/retrieval material into a safe JSON report.

    Pure and side-effect free: it never reads or writes memory, cron, config,
    or the filesystem. ``search_fn`` is injected (never imported) and is only
    consulted for *clean* candidates -- secret-like and noisy material is
    never sent to the search backend. When ``search_fn`` is omitted, no
    duplicate detection is attempted.

    Classification per non-empty candidate (first match wins):

    * ``secret``    -- matches the secret-like patterns; text is **redacted**
      in the report (counts/warnings only, never the literal);
    * ``noisy``     -- matches the temporary/progress patterns;
    * ``duplicate`` -- clean, and ``search_fn`` returned a non-empty result;
    * ``durable``   -- clean, and not a duplicate.

    The result is JSON-serializable and deterministic for a given input and a
    deterministic ``search_fn``. ``extra_warnings`` (e.g. a CLI "input file
    not found") are merged verbatim into ``warnings``.
    """
    counts = {"durable": 0, "noisy": 0, "secret": 0, "duplicate": 0}
    examples: dict[str, list[str]] = {
        "durable": [],
        "noisy": [],
        "secret": [],
        "duplicate": [],
    }
    warnings: list[str] = list(extra_warnings)
    recommendations: list[str] = []
    search_failed = False

    def _add_example(kind: str, text: str) -> None:
        bucket = examples[kind]
        if len(bucket) < _LEAN_EXAMPLE_CAP:
            bucket.append(text)

    for raw in candidates:
        text = _result_text(raw)
        if not text:
            continue

        if _matches_any(_SECRET_PATTERNS, text):
            counts["secret"] += 1
            _add_example("secret", _LEAN_SECRET_PLACEHOLDER)
            continue
        if _matches_any(_TEMPORARY_PATTERNS, text):
            counts["noisy"] += 1
            _add_example("noisy", text)
            continue

        is_duplicate = False
        if search_fn is not None:
            try:
                is_duplicate = bool(search_fn(text))
            except Exception:  # noqa: BLE001 - report, never crash lean-check
                search_failed = True
                is_duplicate = False
        if is_duplicate:
            counts["duplicate"] += 1
            _add_example("duplicate", text)
        else:
            counts["durable"] += 1
            _add_example("durable", text)

    total = sum(counts.values())

    if total == 0:
        warnings.append("no candidate input provided; nothing to lean-check")
    if counts["secret"]:
        warnings.append(
            f"secret-like content found in {counts['secret']} candidate(s); "
            "never persist these"
        )
    if search_failed:
        warnings.append(
            "duplicate search failed for some candidates; "
            "treated them as non-duplicate"
        )

    if total and counts["noisy"] / total > _LEAN_NOISE_RATE:
        recommendations.append(
            "noisy recall looks high; consider tightening what gets mined"
        )
    if total and counts["duplicate"] / total > _LEAN_DUPLICATE_RATE:
        recommendations.append(
            "duplicate rate looks high; memory may be accumulating near-dupes"
        )

    return {
        "report_only": True,
        "total": total,
        "counts": counts,
        "examples": examples,
        "warnings": warnings,
        "recommendations": recommendations,
        "safety": {
            "report_only": True,
            "no_memory_writes": True,
            "no_cron": True,
            "no_obsidian_writes": True,
        },
    }


def render_report(report: DreamReport) -> str:
    """Render a :class:`DreamReport` as deterministic markdown.

    The output depends only on ``report`` (same input -> identical string),
    so it is safe to snapshot, diff, or post in a cron report.
    """
    lines = [
        "# MemPalace Dream Report",
        "",
        f"- Remembered: {report.remembered}",
        f"- Duplicates: {report.duplicates}",
        f"- Rejected: {report.rejected}",
        "",
        "## Remembered",
        "",
    ]
    if report.remembered_texts:
        lines.extend(f"- {text}" for text in report.remembered_texts)
    else:
        lines.append("_None_")
    return "\n".join(lines) + "\n"


def mine_candidates(session_entries: Iterable[str]) -> list[CandidateMemory]:
    """Turn raw session entries into candidate memories.

    Splitting is intentionally simple: one non-empty trimmed line per
    candidate. Scoring/filtering decide what survives.
    """
    candidates: list[CandidateMemory] = []
    for entry in session_entries:
        if entry is None:
            continue
        for line in str(entry).splitlines():
            text = line.strip()
            if not text:
                continue
            candidate = CandidateMemory(text=text)
            candidate.score = score_candidate(candidate)
            candidates.append(candidate)
    return candidates


def score_candidate(candidate: CandidateMemory) -> float:
    """Score a candidate. Higher means more worth persisting.

    Secrets and temporary/progress noise are hard-rejected (negative score);
    durable preference/environment/workflow signals raise the score.
    """
    text = candidate.text

    if _matches_any(_SECRET_PATTERNS, text):
        candidate.rejected = True
        candidate.reason = "secret-like content"
        candidate.score = -1.0
        return candidate.score

    if _matches_any(_TEMPORARY_PATTERNS, text):
        candidate.rejected = True
        candidate.reason = "temporary/progress content"
        candidate.score = -1.0
        return candidate.score

    score = sum(1.0 for p in _DURABLE_PATTERNS if p.search(text))
    candidate.score = score
    if score < _MIN_DURABLE_SCORE:
        candidate.reason = "not a durable fact"
    return score


def filter_durable_candidates(
    candidates: Iterable[CandidateMemory],
) -> list[CandidateMemory]:
    """Keep only non-rejected candidates that clear the durability bar."""
    return [
        c
        for c in candidates
        if not c.rejected and c.score >= _MIN_DURABLE_SCORE
    ]


def dedupe_candidates(
    candidates: Iterable[CandidateMemory],
    search_fn: SearchFn,
) -> list[CandidateMemory]:
    """Drop candidates already present in memory.

    ``search_fn`` is called before any remember. Any non-empty result means
    the candidate is a duplicate and must not be remembered.
    """
    survivors: list[CandidateMemory] = []
    for candidate in candidates:
        results = search_fn(candidate.text)
        if results:
            continue
        survivors.append(candidate)
    return survivors


def remember_candidates(
    candidates: Iterable[CandidateMemory],
    remember_fn: RememberFn,
) -> list[str]:
    """Persist candidates via the injected ``remember_fn``.

    Each candidate's text is normalized (see ``normalize_remembered_text``)
    and passed directly to ``remember_fn``. Returns the list of texts
    actually handed to ``remember_fn``, in order.
    """
    remembered: list[str] = []
    for candidate in candidates:
        text = normalize_remembered_text(candidate.text)
        remember_fn(text)
        remembered.append(text)
    return remembered


def run_light_dream(
    session_entries: Iterable[str],
    search_fn: SearchFn,
    remember_fn: RememberFn,
) -> DreamReport:
    """Run the full light dream pipeline and return a report."""
    candidates = mine_candidates(session_entries)
    durable = filter_durable_candidates(candidates)
    rejected = len(candidates) - len(durable)

    # dedupe_candidates fully exhausts search_fn before we reach
    # remember_candidates, so every search precedes every remember.
    survivors = dedupe_candidates(durable, search_fn)
    duplicates = len(durable) - len(survivors)

    remembered_texts = remember_candidates(survivors, remember_fn)

    return DreamReport(
        remembered=len(remembered_texts),
        duplicates=duplicates,
        rejected=rejected,
        remembered_texts=remembered_texts,
    )
