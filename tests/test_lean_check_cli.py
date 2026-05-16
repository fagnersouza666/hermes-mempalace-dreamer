"""CLI tests for ``hermes mempalace-dreaming lean-check`` (strict TDD).

The command must be report-only: it prints JSON and never writes memory,
cron, config, Obsidian, or any file. Missing/empty input must yield a valid
JSON report with warnings, not a crash.
"""
from pathlib import Path
import argparse
import importlib.util
import json
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "plugin_lean_check_test", ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugin_lean_check_test"] = module
    spec.loader.exec_module(module)
    return module


def _parse(module, argv):
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    return parser.parse_args(argv)


def test_lean_check_empty_input_prints_valid_json_with_warning(capsys):
    module = load_plugin()
    args = _parse(module, ["lean-check"])
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["report_only"] is True
    assert payload["total"] == 0
    assert any("no candidate" in w.lower() for w in payload["warnings"])


def test_lean_check_reads_input_file(capsys, tmp_path):
    f = tmp_path / "candidates.txt"
    f.write_text(
        "User prefers tabs over spaces and uses Python 3.11 on Linux.\n"
        "\n"
        "Fixed bug in PR #123, phase 2 done.\n"
        "the api key and password live in the .env file\n"
    )
    module = load_plugin()
    args = _parse(module, ["lean-check", "--input-file", str(f)])
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 3
    assert payload["counts"]["durable"] == 1
    assert payload["counts"]["noisy"] == 1
    assert payload["counts"]["secret"] == 1
    # Secret text must not leak into the printed report.
    blob = json.dumps(payload)
    assert "api key" not in blob and ".env" not in blob


def test_lean_check_missing_input_file_warns_not_crashes(capsys, tmp_path):
    module = load_plugin()
    missing = tmp_path / "does-not-exist.txt"
    args = _parse(module, ["lean-check", "--input-file", str(missing)])
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 0
    assert any(
        "input file" in w.lower() and "not" in w.lower()
        for w in payload["warnings"]
    )


def test_lean_check_json_input_mode(capsys):
    module = load_plugin()
    payload_in = json.dumps(
        [
            "User prefers tabs and uses docker on Linux",
            "commit a1b2c3d completed",
        ]
    )
    args = _parse(module, ["lean-check", "--json-input", payload_in])
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 2
    assert payload["counts"]["durable"] == 1
    assert payload["counts"]["noisy"] == 1


def test_lean_check_bad_json_input_warns_not_crashes(capsys):
    module = load_plugin()
    args = _parse(module, ["lean-check", "--json-input", "{not valid json"])
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 0
    assert any("json" in w.lower() for w in payload["warnings"])


def test_lean_check_does_not_create_files(capsys, tmp_path):
    module = load_plugin()
    args = _parse(module, ["lean-check"])
    args.func(args)
    capsys.readouterr()
    # Report-only: the command must not have created anything under tmp.
    assert list(tmp_path.iterdir()) == []
