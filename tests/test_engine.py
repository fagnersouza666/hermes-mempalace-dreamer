"""Tests for the pure dreaming engine MVP.

These tests must not require the Hermes runtime. Memory reads/writes are
dependency-injected.
"""
from mempalace_dreaming.engine import (
    CandidateMemory,
    DreamReport,
    mine_candidates,
    score_candidate,
    filter_durable_candidates,
    dedupe_candidates,
    remember_candidates,
    run_light_dream,
    normalize_remembered_text,
)


DURABLE = "User prefers tabs over spaces and uses Python 3.11 on Linux."
TEMP = "Fixed bug in PR #123, phase 2 done, commit a1b2c3d completed."
SECRET = "OPENAI_API_KEY=sk-abcdef stored in .env connection string."


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
        "api key is sk-12345",
        "auth token: ghp_xxxxx",
        "password=hunter2",
        "-----BEGIN PRIVATE KEY-----",
        "values live in .env",
        "postgres connection string: postgres://u:p@h/db",
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
