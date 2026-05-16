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
    count = remember_candidates(durable, remembered_texts.append)
    assert count == len(durable)
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
