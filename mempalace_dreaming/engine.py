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
    re.compile(r"\b(?:sk|ghp|xox[baprs])-?[A-Za-z0-9]{4,}", re.IGNORECASE),
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


def _matches_any(patterns: Iterable[re.Pattern[str]], text: str) -> bool:
    return any(p.search(text) for p in patterns)


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
) -> int:
    """Persist candidates via the injected ``remember_fn``.

    Returns the number of candidates remembered.
    """
    count = 0
    for candidate in candidates:
        remember_fn(candidate.text)
        count += 1
    return count


def run_light_dream(
    session_entries: Iterable[str],
    search_fn: SearchFn,
    remember_fn: RememberFn,
) -> DreamReport:
    """Run the full light dream pipeline and return a report."""
    candidates = mine_candidates(session_entries)
    durable = filter_durable_candidates(candidates)
    rejected = len(candidates) - len(durable)

    survivors = dedupe_candidates(durable, search_fn)
    duplicates = len(durable) - len(survivors)

    remembered_texts: list[str] = []
    remembered = remember_candidates(survivors, remembered_texts.append)
    for text in remembered_texts:
        remember_fn(text)

    return DreamReport(
        remembered=remembered,
        duplicates=duplicates,
        rejected=rejected,
        remembered_texts=remembered_texts,
    )
