"""Tests for the REM-style report-only integration analysis.

`build_integration_report` must be pure and side-effect free: it never reads
or writes memory, cron, config, the filesystem, or Obsidian. It only reports
conservative, deterministic signals.
"""
from pathlib import Path
import argparse
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout

from mempalace_dreaming.engine import build_integration_report

ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "plugin_integration_test", ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugin_integration_test"] = module
    spec.loader.exec_module(module)
    return module


def test_empty_input_is_report_only_with_warning():
    report = build_integration_report([])
    assert report["report_only"] is True
    assert report["analyzed"] == 0
    assert report["contradictions"] == []
    assert report["clusters"] == []
    assert any("nothing to integrate" in w for w in report["warnings"])
    assert report["safety"]["no_memory_writes"] is True
    assert report["safety"]["no_memory_deletes"] is True


def test_opposing_polarity_is_flagged_as_contradiction():
    report = build_integration_report(
        [
            "User always uses tabs in Python",
            "User never uses tabs in Python",
        ]
    )
    assert len(report["contradictions"]) == 1
    c = report["contradictions"][0]
    assert c["left_index"] == 0
    assert c["right_index"] == 1
    assert "python" in c["shared_terms"] and "tabs" in c["shared_terms"]
    assert c["reason"] == "opposing polarity"


def test_antonym_pair_is_flagged_even_without_polarity_words():
    report = build_integration_report(
        [
            "Editor indentation set to tabs in repo",
            "Editor indentation set to spaces in repo",
        ]
    )
    assert len(report["contradictions"]) == 1
    assert report["contradictions"][0]["reason"] == "antonym pair"


def test_supersede_candidate_detected_by_recency_marker():
    report = build_integration_report(
        [
            "Database backups stored weekly",
            "Database backups now stored daily",
        ]
    )
    assert len(report["supersede_candidates"]) == 1
    s = report["supersede_candidates"][0]
    assert s["newer_index"] == 1
    assert s["older_index"] == 0
    assert s["supersede_candidate"] == "Database backups stored weekly"
    assert any("supersede" in r for r in report["recommendations"])


def test_clusters_group_near_duplicate_topics():
    report = build_integration_report(
        [
            "Project uses Postgres for storage",
            "Project uses Postgres for storage layer",
            "Team standup happens every Monday",
        ]
    )
    cluster_keys = [tuple(c["topic_key"]) for c in report["clusters"]]
    assert len(cluster_keys) == 1
    assert all(c["size"] >= 2 for c in report["clusters"])


def test_secret_like_entries_excluded_and_not_echoed():
    report = build_integration_report(
        [
            "User prefers Postgres database",
            "The api key is sk-abcd1234 and password is hunter2",
        ]
    )
    assert report["skipped"]["secret"] == 1
    blob = json.dumps(report)
    assert "sk-abcd1234" not in blob
    assert "hunter2" not in blob
    assert any("secret-like" in w for w in report["warnings"])


def test_noisy_entries_excluded_from_analysis():
    report = build_integration_report(
        [
            "Fixed bug in PR #123, commit a1b2c3d done",
            "User prefers dark mode in the editor",
        ]
    )
    assert report["skipped"]["noisy"] == 1
    assert report["analyzed"] == 1


def test_report_is_deterministic_for_same_input():
    data = [
        "Project always uses Docker for local dev",
        "Project never uses Docker for local dev",
        "Project uses Docker for local dev pipelines",
    ]
    assert build_integration_report(data) == build_integration_report(data)


def test_report_is_json_serializable():
    report = build_integration_report(
        ["User always uses vim", "User never uses vim"]
    )
    json.dumps(report)  # must not raise


def test_cli_integration_report_prints_json(capsys):
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(
        [
            "integration-report",
            "--json-input",
            json.dumps(
                [
                    "User always uses tabs in Python",
                    "User never uses tabs in Python",
                ]
            ),
        ]
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        module._handle_cli(args)
    payload = json.loads(buf.getvalue())
    assert payload["report_only"] is True
    assert len(payload["contradictions"]) == 1


def test_cli_integration_report_bad_json_is_warning_not_crash(capsys):
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(
        ["integration-report", "--json-input", "{not json"]
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        module._handle_cli(args)
    payload = json.loads(buf.getvalue())
    assert payload["analyzed"] == 0
    assert any("not valid JSON" in w for w in payload["warnings"])
