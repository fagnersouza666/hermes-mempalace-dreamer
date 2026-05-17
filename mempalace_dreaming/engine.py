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


# --- REM-style integration analysis (report-only, no writes) --------------
#
# Conservative, deterministic heuristics over already-mined durable material.
# This intentionally does *not* claim semantic intelligence: it is keyword /
# polarity / overlap based, side-effect free, and only ever *reports*. It
# never deletes, supersedes, or rewrites memory; the operator decides.

# Tokens too generic to anchor a "topic" on. Kept tiny and explicit on
# purpose -- a real stemmer would be overclaiming for a report-only helper.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "into", "over",
        "user", "users", "uses", "use", "using", "used", "always", "never",
        "prefer", "prefers", "preferred", "preference", "should", "must",
        "when", "what", "which", "while", "your", "their", "have", "has",
        "are", "was", "were", "will", "not", "but", "all", "any", "via",
        "now", "instead", "longer", "updated", "update", "replace", "replaces",
        "supersede", "supersedes", "deprecated", "default", "by",
    }
)

# Polarity markers. A memory is "negative" if it asserts an avoidance/denial,
# "positive" if it asserts a preference/affirmation. Used only to flag a
# *possible* contradiction between two memories on the same topic.
_NEGATIVE_POLARITY = re.compile(
    r"\b(?:never|avoid|do not|don't|doesn't|stop|disable[d]?|"
    r"no longer|without|reject)\b",
    re.IGNORECASE,
)
_POSITIVE_POLARITY = re.compile(
    r"\b(?:always|prefer[s]?|use[s]?|enable[d]?|require[s]?|"
    r"by default|must|should)\b",
    re.IGNORECASE,
)

# Hand-picked antonym pairs: a near-duplicate topic where these opposing
# tokens appear on each side is a strong contradiction signal even without a
# polarity word (e.g. "tabs" vs "spaces").
_ANTONYM_PAIRS = (
    ("tabs", "spaces"),
    ("tab", "space"),
    ("light", "dark"),
    ("sync", "async"),
    ("synchronous", "asynchronous"),
)

# A memory that supersedes another tends to carry a recency/override marker.
_SUPERSEDE_MARKER = re.compile(
    r"\b(?:now|instead|no longer|as of|updated|supersede[s]?|"
    r"replace[s]?|deprecated|moved to|switched to|migrated to)\b",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+#-]*")
# How many salient tokens form a deterministic topic key.
_TOPIC_KEY_SIZE = 3
# Minimum shared significant tokens for two memories to be "about the same".
_MIN_SHARED_TOKENS = 2
# Cap echoed examples so the report never dumps a corpus.
_INTEGRATION_EXAMPLE_CAP = 5


def _significant_tokens(text: str) -> list[str]:
    """Lowercased, de-duplicated, order-preserving significant tokens.

    Drops stopwords and tokens shorter than three characters. Deterministic
    for a given input.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        tok = raw.strip(".-+#")
        if len(tok) < 3 or tok in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _topic_key(tokens: Sequence[str]) -> tuple[str, ...]:
    """Deterministic topic key: the most salient tokens, sorted stably.

    Salience proxy = longer token first, then alphabetical. Same token set
    always yields the same key, so clustering is reproducible.
    """
    ranked = sorted(set(tokens), key=lambda t: (-len(t), t))
    return tuple(sorted(ranked[:_TOPIC_KEY_SIZE]))


def _antonym_conflict(tokens_a: set[str], tokens_b: set[str]) -> bool:
    for left, right in _ANTONYM_PAIRS:
        if (left in tokens_a and right in tokens_b) or (
            right in tokens_a and left in tokens_b
        ):
            return True
    return False


def build_integration_report(memories: Iterable[object]) -> dict:
    """REM-style report-only integration analysis. Pure, deterministic.

    Inspects already-mined memory material and reports three conservative
    signals -- it never reads or writes memory, cron, config, the filesystem,
    or Obsidian, and it never deletes/supersedes anything itself:

    * ``contradictions`` -- two memories about the same topic (>=2 shared
      significant tokens) with opposing polarity (``always`` vs ``never`` ...)
      or a hand-picked antonym pair (tabs vs spaces ...);
    * ``supersede_candidates`` -- two memories with the same topic key where
      exactly one carries a recency/override marker (``now``/``instead``/
      ``no longer`` ...); the marked one is reported as the likely newer
      statement, the other as the supersede candidate;
    * ``clusters`` -- memories grouped by an identical deterministic topic
      key (size >= 2), surfacing near-duplicate / consolidation candidates.

    Secret-like and temporary/progress entries are excluded from analysis
    (they are not durable facts); their counts are reported and secret text
    is never echoed. Output is JSON-serializable and deterministic for a
    given input. Findings are ordered by input position so diffs are stable.
    """
    items: list[tuple[int, str, list[str], set[str], tuple[str, ...]]] = []
    skipped = {"secret": 0, "noisy": 0, "empty": 0}

    index = 0
    for raw in memories:
        text = _result_text(raw)
        if not text:
            skipped["empty"] += 1
            continue
        if _matches_any(_SECRET_PATTERNS, text):
            skipped["secret"] += 1
            continue
        if _matches_any(_TEMPORARY_PATTERNS, text):
            skipped["noisy"] += 1
            continue
        tokens = _significant_tokens(text)
        if not tokens:
            continue
        token_set = set(tokens)
        items.append(
            (index, text, tokens, token_set, _topic_key(tokens))
        )
        index += 1

    contradictions: list[dict] = []
    supersede_candidates: list[dict] = []

    for a in range(len(items)):
        idx_a, text_a, _, set_a, key_a = items[a]
        for b in range(a + 1, len(items)):
            idx_b, text_b, _, set_b, key_b = items[b]
            shared = sorted(set_a & set_b)
            same_topic = len(shared) >= _MIN_SHARED_TOKENS

            if same_topic or _antonym_conflict(set_a, set_b):
                pol_a = _polarity(text_a)
                pol_b = _polarity(text_b)
                opposing_polarity = (
                    pol_a is not None
                    and pol_b is not None
                    and pol_a != pol_b
                )
                antonym = _antonym_conflict(set_a, set_b)
                if same_topic and (opposing_polarity or antonym):
                    contradictions.append(
                        {
                            "left_index": idx_a,
                            "right_index": idx_b,
                            "left": text_a,
                            "right": text_b,
                            "shared_terms": shared,
                            "reason": (
                                "antonym pair"
                                if antonym
                                else "opposing polarity"
                            ),
                        }
                    )

            if key_a and key_a == key_b:
                marked_a = bool(_SUPERSEDE_MARKER.search(text_a))
                marked_b = bool(_SUPERSEDE_MARKER.search(text_b))
                if marked_a != marked_b:
                    newer_idx, newer = (
                        (idx_a, text_a) if marked_a else (idx_b, text_b)
                    )
                    older_idx, older = (
                        (idx_b, text_b) if marked_a else (idx_a, text_a)
                    )
                    supersede_candidates.append(
                        {
                            "newer_index": newer_idx,
                            "older_index": older_idx,
                            "newer": newer,
                            "supersede_candidate": older,
                            "topic_key": list(key_a),
                        }
                    )

    buckets: dict[tuple[str, ...], list[str]] = {}
    for _, text, _, _, key in items:
        if key:
            buckets.setdefault(key, []).append(text)
    clusters = [
        {
            "topic_key": list(key),
            "size": len(texts),
            "members": texts[:_INTEGRATION_EXAMPLE_CAP],
        }
        for key, texts in sorted(buckets.items())
        if len(texts) >= 2
    ]

    warnings: list[str] = []
    if not items:
        warnings.append(
            "no durable memory material to analyze; nothing to integrate"
        )
    if skipped["secret"]:
        warnings.append(
            f"excluded {skipped['secret']} secret-like entr(y/ies) from "
            "integration analysis; never persist or echo these"
        )

    recommendations: list[str] = []
    if contradictions:
        recommendations.append(
            "review contradictions and keep the correct memory; "
            "resolution is manual and report-first"
        )
    if supersede_candidates:
        recommendations.append(
            "review supersede candidates; older statements may be stale "
            "but are never auto-removed"
        )
    if clusters:
        recommendations.append(
            "near-duplicate clusters found; consider consolidating after "
            "explicit review"
        )

    return {
        "report_only": True,
        "analyzed": len(items),
        "skipped": skipped,
        "contradictions": contradictions,
        "supersede_candidates": supersede_candidates,
        "clusters": clusters,
        "warnings": warnings,
        "recommendations": recommendations,
        "safety": {
            "report_only": True,
            "no_memory_writes": True,
            "no_memory_deletes": True,
            "no_cron": True,
            "no_obsidian_writes": True,
        },
    }


def _polarity(text: str) -> str | None:
    """``"neg"`` / ``"pos"`` / ``None``. Negative wins ties (more specific)."""
    if _NEGATIVE_POLARITY.search(text):
        return "neg"
    if _POSITIVE_POLARITY.search(text):
        return "pos"
    return None


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
