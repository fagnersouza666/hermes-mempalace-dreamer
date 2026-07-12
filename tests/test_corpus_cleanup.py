"""Corpus cleanup/migration tooling tests (strict TDD — written first).

The live corpus accumulated cron-session transcripts, low-value maintenance
reports, and exact content repeats across sessions (see
tests/test_provider_sync_quality.py for the ingestion-side fix). This tool
migrates an *existing* corpus:

* ``build_corpus_cleanup_plan`` — read-only scan that classifies turn files
  (background-session / low-value / duplicate-content) and never mutates
  anything;
* ``apply_corpus_cleanup`` — dry-run by default; with ``apply=True`` it
  MOVES planned files into a backup directory (never deletes) and rebuilds
  the dedup marker index for the kept files. The palace is never touched.
"""
from pathlib import Path
import json

import pytest

from mempalace_dreaming.cleanup import (
    apply_corpus_cleanup,
    build_corpus_cleanup_plan,
)


def _write_turn(corpus, name, *, session_id, platform, user, assistant):
    turns = Path(corpus, "turns")
    turns.mkdir(parents=True, exist_ok=True)
    body = (
        f"# Hermes Turn\n\n"
        f"- session_id: {session_id}\n"
        f"- timestamp: 2026-05-19T08:31:23+00:00\n"
        f"- platform: {platform}\n\n"
        f"## User\n\n{user}\n\n"
        f"## Assistant\n\n{assistant}\n"
    )
    path = turns / name
    path.write_text(body, encoding="utf-8")
    return path


USER_A = "Mestre pediu para documentar a decisão de usar filas no gateway."
ASSISTANT_A = "Documentei: filas desacoplam o gateway do faturamento."
USER_B = "Qual foi a decisão sobre o banco de dados do projeto Hermes?"
ASSISTANT_B = "Decidimos usar PostgreSQL com particionamento mensal."


@pytest.fixture()
def corpus(tmp_path):
    corpus = tmp_path / "corpus"
    _write_turn(
        corpus, "turn-20260516T010000-20260516-aaaaaaaaaaaa.md",
        session_id="20260516_010101_aaaa", platform="telegram",
        user=USER_A, assistant=ASSISTANT_A,
    )
    # Exact duplicate content, later file, different session.
    _write_turn(
        corpus, "turn-20260517T010000-20260517-bbbbbbbbbbbb.md",
        session_id="20260517_010101_bbbb", platform="telegram",
        user=USER_A, assistant=ASSISTANT_A,
    )
    # Materially different — must be kept.
    _write_turn(
        corpus, "turn-20260518T010000-20260518-cccccccccccc.md",
        session_id="20260518_010101_cccc", platform="cli",
        user=USER_B, assistant=ASSISTANT_B,
    )
    # Cron session — remove (background).
    _write_turn(
        corpus, "turn-20260519T083123-cron_86e-dddddddddddd.md",
        session_id="cron_86ebf7425e3c_20260519_083051", platform="cron",
        user="rotina diária de dreaming", assistant="- memórias salvas: nenhuma.",
    )
    # Low-value maintenance reply on a primary platform — remove.
    _write_turn(
        corpus, "turn-20260520T010000-20260520-eeeeeeeeeeee.md",
        session_id="20260520_010101_eeee", platform="telegram",
        user="Execute a rotina de dreaming agora.",
        assistant="Sem novos fatos duráveis hoje.",
    )
    return corpus


def test_plan_is_read_only_and_classifies(corpus):
    before = sorted(p.name for p in Path(corpus, "turns").iterdir())
    plan = build_corpus_cleanup_plan(corpus)
    after = sorted(p.name for p in Path(corpus, "turns").iterdir())

    assert before == after, "plan must not mutate the corpus"
    assert plan["report_only"] is True
    assert plan["scanned"] == 5

    reasons = {Path(e["file"]).name: e["reason"] for e in plan["remove"]}
    assert reasons["turn-20260517T010000-20260517-bbbbbbbbbbbb.md"] == "duplicate-content"
    assert reasons["turn-20260519T083123-cron_86e-dddddddddddd.md"] == "background-session"
    assert reasons["turn-20260520T010000-20260520-eeeeeeeeeeee.md"] == "low-value"

    kept = {Path(f).name for f in plan["keep"]}
    assert "turn-20260516T010000-20260516-aaaaaaaaaaaa.md" in kept
    assert "turn-20260518T010000-20260518-cccccccccccc.md" in kept


def test_duplicate_keeps_the_earliest_file(corpus):
    plan = build_corpus_cleanup_plan(corpus)
    kept = {Path(f).name for f in plan["keep"]}
    assert "turn-20260516T010000-20260516-aaaaaaaaaaaa.md" in kept
    removed = {Path(e["file"]).name for e in plan["remove"]}
    assert "turn-20260517T010000-20260517-bbbbbbbbbbbb.md" in removed


def test_unparseable_file_is_kept_with_warning(corpus):
    weird = Path(corpus, "turns", "turn-20260521T010000-20260521-ffffffffffff.md")
    weird.write_text("not a hermes turn at all", encoding="utf-8")
    plan = build_corpus_cleanup_plan(corpus)
    assert str(weird) in plan["keep"]
    assert any("unparseable" in w or "parse" in w for w in plan["warnings"])


def test_missing_corpus_degrades_to_warning(tmp_path):
    plan = build_corpus_cleanup_plan(tmp_path / "nope")
    assert plan["scanned"] == 0
    assert plan["remove"] == []
    assert plan["warnings"]


def test_plan_is_json_serializable(corpus):
    plan = build_corpus_cleanup_plan(corpus)
    json.dumps(plan)


# ---------------------------------------------------------------------------
# apply_corpus_cleanup
# ---------------------------------------------------------------------------


def test_apply_default_is_dry_run(corpus):
    plan = build_corpus_cleanup_plan(corpus)
    result = apply_corpus_cleanup(plan)
    assert result["applied"] is False
    assert result["moved"] == []
    # Nothing changed on disk.
    assert len(list(Path(corpus, "turns").glob("turn-*.md"))) == 5


def test_apply_moves_to_backup_never_deletes(corpus, tmp_path):
    plan = build_corpus_cleanup_plan(corpus)
    backup = tmp_path / "backup"
    result = apply_corpus_cleanup(plan, apply=True, backup_dir=backup)

    assert result["applied"] is True
    assert result["backup_dir"] == str(backup)
    assert len(result["moved"]) == 3

    remaining = sorted(p.name for p in Path(corpus, "turns").glob("turn-*.md"))
    assert remaining == [
        "turn-20260516T010000-20260516-aaaaaaaaaaaa.md",
        "turn-20260518T010000-20260518-cccccccccccc.md",
    ]
    backed_up = sorted(p.name for p in Path(backup, "turns").glob("turn-*.md"))
    assert backed_up == [
        "turn-20260517T010000-20260517-bbbbbbbbbbbb.md",
        "turn-20260519T083123-cron_86e-dddddddddddd.md",
        "turn-20260520T010000-20260520-eeeeeeeeeeee.md",
    ]


def test_apply_rebuilds_dedup_index_for_kept_files(corpus, tmp_path):
    plan = build_corpus_cleanup_plan(corpus)
    result = apply_corpus_cleanup(
        plan, apply=True, backup_dir=tmp_path / "backup"
    )
    index = Path(corpus, "turns", ".dedup-index")
    markers = list(index.iterdir())
    assert result["index_markers_created"] == 2
    assert len(markers) == 2


def test_apply_clears_stale_dedup_markers(corpus, tmp_path):
    # A marker left over for content that the cleanup moves to backup must
    # not survive the rebuild — it would silently block that content from
    # ever being filed again.
    index = Path(corpus, "turns", ".dedup-index")
    index.mkdir(parents=True)
    stale = index / ("f" * 24)
    stale.write_text("stale marker\n", encoding="utf-8")

    plan = build_corpus_cleanup_plan(corpus)
    result = apply_corpus_cleanup(
        plan, apply=True, backup_dir=tmp_path / "backup"
    )

    assert not stale.exists()
    assert result["index_markers_cleared"] == 1
    assert result["index_markers_created"] == 2
    assert len(list(index.iterdir())) == 2


def test_apply_never_touches_the_palace(corpus, tmp_path):
    palace = tmp_path / "palace"
    palace.mkdir()
    sentinel = palace / "segment.bin"
    sentinel.write_bytes(b"hnsw")
    plan = build_corpus_cleanup_plan(corpus)
    apply_corpus_cleanup(plan, apply=True, backup_dir=tmp_path / "backup")
    assert sentinel.read_bytes() == b"hnsw"
    assert list(palace.iterdir()) == [sentinel]


def test_apply_result_is_json_serializable(corpus, tmp_path):
    plan = build_corpus_cleanup_plan(corpus)
    result = apply_corpus_cleanup(
        plan, apply=True, backup_dir=tmp_path / "backup"
    )
    json.dumps(result)
    assert result["errors"] == []


# ---------------------------------------------------------------------------
# CLI subcommand
# ---------------------------------------------------------------------------


def _load_plugin_module():
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "plugin_cleanup_cli_test", root / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_cli(argv):
    import argparse

    module = _load_plugin_module()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(argv)
    return module, args


def test_cli_corpus_cleanup_dry_run_by_default(corpus, capsys):
    module, args = _run_cli(["corpus-cleanup", "--corpus-path", str(corpus)])
    module._handle_cli(args)
    out = json.loads(capsys.readouterr().out)
    assert out["plan"]["report_only"] is True
    assert out["result"]["applied"] is False
    assert len(list(Path(corpus, "turns").glob("turn-*.md"))) == 5


def test_cli_corpus_cleanup_apply_moves_to_backup(corpus, tmp_path, capsys):
    backup = tmp_path / "cli-backup"
    module, args = _run_cli([
        "corpus-cleanup", "--corpus-path", str(corpus),
        "--apply", "--backup-dir", str(backup),
    ])
    module._handle_cli(args)
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["applied"] is True
    assert len(list(Path(corpus, "turns").glob("turn-*.md"))) == 2
    assert len(list(Path(backup, "turns").glob("turn-*.md"))) == 3
