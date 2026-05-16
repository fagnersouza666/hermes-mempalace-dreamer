"""Tests for the pure dreaming engine MVP.

These tests must not require the Hermes runtime. Memory reads/writes are
dependency-injected.
"""
from mempalace_dreaming.engine import (
    CandidateMemory,
    DreamReport,
    RetrievalAuditReport,
    mine_candidates,
    score_candidate,
    filter_durable_candidates,
    dedupe_candidates,
    remember_candidates,
    run_light_dream,
    normalize_remembered_text,
    render_report,
    audit_retrieval_noise,
)


DURABLE = "User prefers tabs over spaces and uses Python 3.11 on Linux."
TEMP = "Fixed bug in PR #123, phase 2 done, commit a1b2c3d completed."
SECRET = "The api key and password live in the .env connection string."


def test_durable_candidate_is_mined_scored_and_remembered():
    candidates = mine_candidates([DURABLE])
    assert candidates, "durable entry should yield a candidate"
    assert all(isinstance(c, CandidateMemory) for c in candidates)
    assert score_candidate(candidates[0]) > 0
    durable = filter_durable_candidates(candidates)
    assert durable, "durable candidate must survive filtering"

    remembered_texts = []
    result = remember_candidates(durable, remembered_texts.append)
    assert result == remembered_texts, "return value is exactly the texts passed to remember_fn"
    assert len(result) == len(durable)
    assert remembered_texts and "Python 3.11" in remembered_texts[0]


def test_temporary_progress_content_is_rejected():
    candidates = mine_candidates([TEMP])
    assert filter_durable_candidates(candidates) == []
    for line in [
        "Merged PR #42",
        "closes issue #99",
        "commit deadbeef1234",
        "branch is 3 commits ahead",
        "fixed the flaky test",
        "task done",
        "phase 3 completed",
        "2024-01-01 12:00:00 INFO starting up",
    ]:
        cands = mine_candidates([line])
        assert filter_durable_candidates(cands) == [], f"should reject: {line!r}"


def test_secrets_are_rejected():
    for line in [
        "the api key is configured elsewhere",
        "an auth token is required here",
        "the password is omitted on purpose",
        "this references a private key",
        "values live in the .env file",
        "the postgres connection string is in deploy config",
    ]:
        cands = mine_candidates([line])
        assert filter_durable_candidates(cands) == [], f"should reject secret: {line!r}"


def test_dedupe_prevents_remembering():
    candidates = filter_durable_candidates(mine_candidates([DURABLE]))
    assert candidates

    def search_hit(query):
        return [{"text": "already stored"}]

    survivors = dedupe_candidates(candidates, search_hit)
    assert survivors == []

    def search_miss(query):
        return []

    assert dedupe_candidates(candidates, search_miss) == candidates


def test_run_light_dream_returns_report_with_counts():
    def search_miss(query):
        return []

    remembered_texts = []
    report = run_light_dream(
        [DURABLE, TEMP, SECRET],
        search_fn=search_miss,
        remember_fn=remembered_texts.append,
    )
    assert isinstance(report, DreamReport)
    assert report.remembered == 1
    assert report.rejected == 2
    assert report.duplicates == 0
    assert len(remembered_texts) == 1


def test_run_light_dream_counts_duplicates():
    def search_hit(query):
        return [{"text": "dup"}]

    remembered_texts = []
    report = run_light_dream(
        [DURABLE],
        search_fn=search_hit,
        remember_fn=remembered_texts.append,
    )
    assert report.remembered == 0
    assert report.duplicates == 1
    assert report.rejected == 0
    assert remembered_texts == []


# --- normalization of remembered text -------------------------------------


def test_normalize_collapses_repeated_whitespace_and_newlines():
    raw = "  prefers   tabs\n\n\tover    spaces  \n  always  "
    assert normalize_remembered_text(raw) == "prefers tabs over spaces always"


def test_normalize_strips_leading_role_labels_and_bullets():
    assert normalize_remembered_text("User: prefers dark mode") == "prefers dark mode"
    assert normalize_remembered_text("Assistant:   uses Python 3.11") == "uses Python 3.11"
    assert normalize_remembered_text("- prefers tabs always") == "prefers tabs always"
    assert normalize_remembered_text("* User: runs on Linux") == "runs on Linux"
    assert normalize_remembered_text("• Assistant: uses docker") == "uses docker"
    # a non-label that merely starts with a role-ish word must be preserved
    assert normalize_remembered_text("Users prefer tabs") == "Users prefer tabs"


def test_normalize_caps_length_at_280_with_ellipsis():
    short = "prefers tabs over spaces"
    assert normalize_remembered_text(short) == short

    long = "x" * 400
    out = normalize_remembered_text(long)
    assert len(out) <= 280
    assert out.endswith("...")
    assert out.startswith("x")

    exactly = "y" * 280
    assert normalize_remembered_text(exactly) == exactly
    assert not normalize_remembered_text(exactly).endswith("...")


# --- remember_candidates contract -----------------------------------------


def test_remember_candidates_passes_normalized_text_directly_to_remember_fn():
    candidates = [
        CandidateMemory(text="User:   prefers   tabs\n\n  over spaces"),
        CandidateMemory(text="- always uses Python 3.11"),
    ]
    seen = []
    result = remember_candidates(candidates, seen.append)

    assert seen == ["prefers tabs over spaces", "always uses Python 3.11"]
    assert result == seen, "remember_candidates returns exactly the texts passed to remember_fn"


# --- run_light_dream ordering + report fidelity ---------------------------


def test_run_light_dream_searches_before_any_remember():
    calls = []

    def search_fn(query):
        calls.append(("search", query))
        return []

    def remember_fn(text):
        calls.append(("remember", text))

    run_light_dream([DURABLE], search_fn=search_fn, remember_fn=remember_fn)

    kinds = [k for k, _ in calls]
    assert "search" in kinds and "remember" in kinds
    last_search = max(i for i, k in enumerate(kinds) if k == "search")
    first_remember = min(i for i, k in enumerate(kinds) if k == "remember")
    assert last_search < first_remember, "every search must precede every remember"


def test_run_light_dream_report_texts_match_remember_fn_exactly():
    passed = []

    def search_miss(query):
        return []

    report = run_light_dream(
        ["  User:  prefers tabs   over spaces and uses Python 3.11 on Linux  "],
        search_fn=search_miss,
        remember_fn=passed.append,
    )
    assert report.remembered == 1
    assert report.remembered_texts == passed
    assert report.remembered_texts == [
        "prefers tabs over spaces and uses Python 3.11 on Linux"
    ]


# --- render_report (deterministic markdown) -------------------------------


def test_render_report_is_deterministic_markdown():
    report = DreamReport(
        remembered=2,
        duplicates=1,
        rejected=3,
        remembered_texts=["prefers tabs", "uses Python 3.11"],
    )
    out = render_report(report)
    assert isinstance(out, str)
    assert "Remembered: 2" in out
    assert "Duplicates: 1" in out
    assert "Rejected: 3" in out
    assert "- prefers tabs" in out
    assert "- uses Python 3.11" in out
    # Deterministic: same input -> byte-identical output.
    assert render_report(report) == out


def test_render_report_handles_no_remembered_texts():
    report = DreamReport(remembered=0, duplicates=0, rejected=0)
    out = render_report(report)
    assert "Remembered: 0" in out
    assert "Duplicates: 0" in out
    assert "Rejected: 0" in out
    # Honest about an empty run.
    assert "_None_" in out


# --- audit_retrieval_noise (pure, no memory writes) -----------------------


def test_audit_retrieval_noise_classifies_useful_vs_noisy():
    results = [
        {"text": "User prefers tabs over spaces"},
        {"text": "Fixed bug in PR #123"},
        {"text": "the api key and password are in the .env file"},
        "uses Python 3.11 on Linux",
        "commit a1b2c3d completed",
    ]
    audit = audit_retrieval_noise(results)

    assert isinstance(audit, RetrievalAuditReport)
    assert "User prefers tabs over spaces" in audit.useful
    assert "uses Python 3.11 on Linux" in audit.useful
    assert "Fixed bug in PR #123" in audit.noisy
    assert "the api key and password are in the .env file" in audit.noisy
    assert "commit a1b2c3d completed" in audit.noisy

    assert audit.total == 5
    assert audit.useful_count == 2
    assert audit.noisy_count == 3
    assert audit.useful_count + audit.noisy_count == audit.total


def test_audit_retrieval_noise_is_pure_and_writes_nothing():
    results = ["User prefers dark mode"]
    audit = audit_retrieval_noise(results)
    assert audit.useful == ["User prefers dark mode"]
    assert audit.noisy == []
    # Called twice -> identical result, proving no hidden state/side effects.
    assert audit_retrieval_noise(results) == audit


def test_audit_retrieval_noise_skips_empty_results():
    audit = audit_retrieval_noise(["", {"text": ""}, None, "uses docker"])
    assert audit.total == 1
    assert audit.useful == ["uses docker"]


# --- realistic-format secret regexes --------------------------------------
#
# These exercise the token/credential-URL regexes in engine.py without
# committing scanner-bait literals to source. Every secret-shaped string is
# assembled at runtime from fragments / chr() / repetition, so a public-repo
# secret scanner sees no full token in this file. Assertions stay
# behavior-focused: the carrier sentence below ("User prefers ...") would
# otherwise score as a durable fact, so rejection proves the secret regex
# fired, and the audit must classify the same text as noisy.

_DASH = chr(45)    # "-"
_UNDER = chr(95)   # "_"
_COLON = chr(58)   # ":"
_AT = chr(64)      # "@"


def _alnum(length: int) -> str:
    """A realistic-looking [A-Za-z0-9] body of the requested length."""
    return ("aZ09" * (length // 4 + 1))[:length]


def _carrier(secret: str) -> str:
    # Durable-sounding wrapper so a pass would require the secret regex to
    # lose to _DURABLE_PATTERNS; rejection therefore isolates the regex.
    return "User prefers using the value " + secret + " on Linux"


def _assert_rejected_as_secret(line: str) -> None:
    candidates = mine_candidates([line])
    assert candidates, "carrier line should mine to a candidate"
    assert filter_durable_candidates(candidates) == []
    assert all(
        c.rejected and c.reason == "secret-like content" for c in candidates
    ), "must be rejected via the secret path, not temporary/durable scoring"
    audit = audit_retrieval_noise([line])
    assert audit.noisy == [line] and audit.useful == []


def test_sk_style_token_is_rejected_as_secret():
    # OpenAI-style: "sk" + "-" + body.
    token = "s" + "k" + _DASH + _alnum(40)
    _assert_rejected_as_secret(_carrier(token))


def test_github_token_like_value_is_rejected_as_secret():
    # Real GitHub classic token shape: the "ghp" prefix, an underscore
    # separator, then a 36-char base62 body. Assembled from fragments so
    # no full token literal lands in source for a scanner to flag.
    token = "g" + "h" + "p" + _UNDER + _alnum(36)
    _assert_rejected_as_secret(_carrier(token))


def test_slack_token_like_value_is_rejected_as_secret():
    # Slack-style: "xox" + class char + "-" + body (the xox[baprs] branch).
    token = "xox" + "b" + _DASH + _alnum(24)
    _assert_rejected_as_secret(_carrier(token))


def test_credentials_embedded_in_url_are_rejected_as_secret():
    # scheme://user:secret@host — assembled so no full credential URL
    # literal appears in source.
    url = (
        "postgres"
        + _COLON
        + "//"
        + "admin"
        + _COLON
        + _alnum(12)
        + _AT
        + "db.internal/app"
    )
    _assert_rejected_as_secret(_carrier(url))
