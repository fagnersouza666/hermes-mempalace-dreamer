"""Tests for the pure ``build_lean_check_report`` helper (strict TDD).

``build_lean_check_report`` must be a pure, dependency-injected helper:

* it never reads or writes memory, cron, config, or the filesystem;
* ``search_fn`` (duplicate detection) is injected, never imported;
* output is JSON-serializable and deterministic (same input -> same dict);
* secret-like material is classified but never echoed back into the report.
"""
import json

from mempalace_dreaming.engine import build_lean_check_report

DURABLE = "User prefers tabs over spaces and uses Python 3.11 on Linux."
DURABLE_2 = "Always runs the test suite on docker before pushing."
NOISY = "Fixed bug in PR #123, phase 2 done, commit a1b2c3d completed."
SECRET = "the api key and password live in the .env connection string"


def test_report_is_json_serializable_and_deterministic():
    result = build_lean_check_report([DURABLE, NOISY, SECRET])
    # JSON-serializable.
    json.dumps(result)
    assert result["report_only"] is True
    # Deterministic: same input -> identical dict.
    assert build_lean_check_report([DURABLE, NOISY, SECRET]) == result


def test_classifies_durable_noisy_and_secret():
    result = build_lean_check_report([DURABLE, DURABLE_2, NOISY, SECRET])
    counts = result["counts"]
    assert counts["durable"] == 2
    assert counts["noisy"] == 1
    assert counts["secret"] == 1
    assert counts["duplicate"] == 0
    assert result["total"] == 4
    assert sum(counts.values()) == result["total"]


def test_examples_include_durable_and_noisy_text_but_redact_secrets():
    result = build_lean_check_report([DURABLE, NOISY, SECRET])
    examples = result["examples"]
    assert DURABLE in examples["durable"]
    assert NOISY in examples["noisy"]
    # The secret text must never be echoed back verbatim anywhere.
    blob = json.dumps(result)
    assert "api key" not in blob
    assert ".env" not in blob
    assert examples["secret"], "secret category should still have an example"
    assert all("redact" in e.lower() for e in examples["secret"])


def test_duplicate_detection_via_injected_search_fn():
    def search_hit(query):
        return [{"text": "already stored"}]

    result = build_lean_check_report([DURABLE, DURABLE_2], search_fn=search_hit)
    assert result["counts"]["duplicate"] == 2
    assert result["counts"]["durable"] == 0
    assert result["examples"]["duplicate"]

    def search_miss(query):
        return []

    miss = build_lean_check_report([DURABLE, DURABLE_2], search_fn=search_miss)
    assert miss["counts"]["duplicate"] == 0
    assert miss["counts"]["durable"] == 2


def test_search_fn_is_only_called_for_clean_candidates():
    seen = []

    def search_fn(query):
        seen.append(query)
        return []

    build_lean_check_report([DURABLE, NOISY, SECRET], search_fn=search_fn)
    # Secrets/noise must never be sent to the search backend.
    assert seen == [DURABLE]


def test_failing_search_fn_does_not_crash():
    def boom(query):
        raise RuntimeError("backend down")

    result = build_lean_check_report([DURABLE], search_fn=boom)
    # Falls back to treating it as non-duplicate, records a warning.
    assert result["counts"]["duplicate"] == 0
    assert result["counts"]["durable"] == 1
    assert any("search" in w.lower() for w in result["warnings"])


def test_empty_input_returns_valid_report_with_warning():
    result = build_lean_check_report([])
    json.dumps(result)
    assert result["total"] == 0
    assert result["counts"] == {
        "durable": 0,
        "noisy": 0,
        "secret": 0,
        "duplicate": 0,
    }
    assert any("no candidate" in w.lower() for w in result["warnings"])


def test_secret_presence_emits_warning():
    result = build_lean_check_report([DURABLE, SECRET])
    assert any("secret" in w.lower() for w in result["warnings"])


def test_high_noise_rate_is_flagged_as_recommendation():
    noisy_inputs = [NOISY, NOISY, NOISY, DURABLE]
    result = build_lean_check_report(noisy_inputs)
    assert any("nois" in r.lower() for r in result["recommendations"])


def test_high_duplicate_rate_is_flagged_as_recommendation():
    def search_hit(query):
        return [{"text": "dup"}]

    result = build_lean_check_report(
        [DURABLE, DURABLE_2], search_fn=search_hit
    )
    assert any("duplicate" in r.lower() for r in result["recommendations"])


def test_accepts_dict_and_object_shaped_results():
    class R:
        def __init__(self, text):
            self.text = text

    result = build_lean_check_report(
        [{"text": DURABLE}, R(NOISY), "uses docker on Linux"]
    )
    assert result["total"] == 3
    assert result["counts"]["durable"] == 2
    assert result["counts"]["noisy"] == 1


def test_blank_entries_are_skipped_not_counted():
    result = build_lean_check_report(["", None, {"text": ""}, DURABLE])
    assert result["total"] == 1
    assert result["counts"]["durable"] == 1


def test_extra_warnings_are_merged_and_report_stays_safe():
    result = build_lean_check_report(
        [DURABLE], extra_warnings=["input file not found: /nope"]
    )
    assert "input file not found: /nope" in result["warnings"]
    safety = result["safety"]
    assert safety["report_only"] is True
    assert safety["no_memory_writes"] is True
    assert safety["no_cron"] is True
    assert safety["no_obsidian_writes"] is True


def test_examples_are_capped_and_order_preserving():
    many = [f"User prefers setting number {i} on Linux" for i in range(20)]
    result = build_lean_check_report(many)
    durable_examples = result["examples"]["durable"]
    assert len(durable_examples) <= 5
    assert durable_examples[0] == many[0]
