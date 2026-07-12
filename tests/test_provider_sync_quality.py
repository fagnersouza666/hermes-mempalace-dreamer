"""Regression tests for memory-quality defects in the bundled provider.

Root cause (observed in a real corpus): the Hermes core cron scheduler
creates agents with ``agent_context="primary"`` hardcoded (upstream
NousResearch/hermes-agent#9763), so the provider's old
``agent_context``-only guard filed every cron run into the corpus. Those
turns embed the full skill text verbatim, so the same content was filed
day after day under different ``cron_*`` session ids, polluting retrieval.

These tests pin the fixes:

* ``sync_turn`` hardening — platform ("cron"), session-id prefix
  (``cron_``) and the cron delivery wrapper in the user content all skip
  filing, while primary Telegram/CLI turns keep being filed;
* low-value maintenance/report filtering — ``[SILENT]``, "Sem novos
  fatos duráveis", "Sem limpeza segura na memória curta", dream/cleanup
  report wrappers;
* normalized content dedup across sessions — same content under a
  different session id is filed once; materially different turns are
  preserved; the dedup claim is atomic so concurrent processes cannot
  both file the same content; no unbounded corpus scan per turn.

The provider module imports the Hermes runtime (``agent.memory_provider``
and ``tools.registry``), which is not available in this repo's test
environment, so both are stubbed before loading the file directly.
"""
from pathlib import Path
import importlib.util
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_PATH = ROOT / "mempalace_dreaming" / "provider_bundle" / "provider_init.py"


def _install_runtime_stubs():
    agent_pkg = types.ModuleType("agent")
    memory_provider = types.ModuleType("agent.memory_provider")

    class MemoryProvider:  # minimal ABC stand-in
        pass

    memory_provider.MemoryProvider = MemoryProvider
    agent_pkg.memory_provider = memory_provider

    tools_pkg = types.ModuleType("tools")
    registry = types.ModuleType("tools.registry")
    registry.tool_error = lambda message, **kwargs: f"ERROR: {message}"
    tools_pkg.registry = registry

    sys.modules.setdefault("agent", agent_pkg)
    sys.modules["agent.memory_provider"] = memory_provider
    sys.modules.setdefault("tools", tools_pkg)
    sys.modules["tools.registry"] = registry


def load_provider_module():
    _install_runtime_stubs()
    spec = importlib.util.spec_from_file_location(
        "mempalace_provider_under_test", PROVIDER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


USER_TEXT = (
    "Mestre pediu para revisar a arquitetura do gateway de pagamentos "
    "e documentar a decisão de usar filas."
)
ASSISTANT_TEXT = (
    "Revisei a arquitetura: a decisão foi usar filas para desacoplar o "
    "gateway de pagamentos do faturamento, com retries idempotentes."
)

CRON_WRAPPER_USER = (
    "[IMPORTANT: You are running as a scheduled cron job. DELIVERY: Your "
    "final response will be automatically delivered to the user — do NOT "
    "use send_message. SILENT: If there is genuinely nothing new to "
    "report, respond with exactly \"[SILENT]\".]\n\n"
    "Execute uma rotina diária conservadora de MemPalace Dreaming."
)


@pytest.fixture()
def provider_factory(tmp_path):
    module = load_provider_module()

    def make(session_id="20260710_121500_abcd", platform="telegram",
             agent_context="primary", corpus=None, **config_overrides):
        config = dict(module._DEFAULTS)
        config["cli_path"] = sys.executable  # absolute + exists => provider active
        config["palace_path"] = str(tmp_path / "palace")
        config["corpus_path"] = str(corpus or tmp_path / "corpus")
        config.update(config_overrides)
        provider = module.MemPalaceMemoryProvider(config=config)
        provider.initialize(
            session_id,
            platform=platform,
            hermes_home=str(tmp_path / "home"),
            agent_context=agent_context,
        )
        # Never shell out to the fake CLI during tests.
        provider._mine_calls = 0

        def _fake_mine():
            provider._mine_calls += 1

        provider._mine_corpus_async = _fake_mine
        return provider

    make.module = module
    return make


def _turn_files(provider):
    return sorted(
        p for p in Path(provider._corpus, "turns").glob("turn-*.md")
    )


# ---------------------------------------------------------------------------
# Background/cron session hardening
# ---------------------------------------------------------------------------


def test_primary_telegram_turn_is_filed(provider_factory):
    provider = provider_factory(platform="telegram")
    provider.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    assert len(_turn_files(provider)) == 1
    assert provider._mine_calls == 1


def test_primary_cli_turn_is_filed(provider_factory):
    provider = provider_factory(platform="cli")
    provider.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    assert len(_turn_files(provider)) == 1


def test_cron_platform_is_skipped_even_with_primary_agent_context(
    provider_factory,
):
    # Upstream #9763: the cron scheduler hardcodes agent_context="primary",
    # so platform must be honored as an independent signal.
    provider = provider_factory(platform="cron", agent_context="primary")
    provider.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    assert _turn_files(provider) == []
    assert provider._mine_calls == 0


def test_cron_session_id_prefix_is_skipped(provider_factory):
    provider = provider_factory(
        session_id="cron_86ebf7425e3c_20260519_083051",
        platform="telegram",  # even if platform is mis-reported
        agent_context="primary",
    )
    provider.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    assert _turn_files(provider) == []


def test_cron_delivery_wrapper_in_user_content_is_skipped(provider_factory):
    provider = provider_factory(platform="telegram", agent_context="primary")
    provider.sync_turn(CRON_WRAPPER_USER, ASSISTANT_TEXT)
    assert _turn_files(provider) == []


def test_subagent_agent_context_still_skipped(provider_factory):
    provider = provider_factory(agent_context="subagent")
    provider.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    assert _turn_files(provider) == []


def test_platform_skip_list_is_configurable(provider_factory):
    provider = provider_factory(
        platform="webhook", sync_skip_platforms=["cron", "webhook"]
    )
    provider.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    assert _turn_files(provider) == []


# ---------------------------------------------------------------------------
# Low-value maintenance/report filtering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "assistant",
    [
        "[SILENT]",
        "  [silent]  ",
        "Sem novos fatos duráveis hoje.",
        "Sem novos fatos duraveis hoje.",
        "Sem limpeza segura na memória curta hoje.",
    ],
)
def test_low_value_assistant_replies_are_skipped(provider_factory, assistant):
    provider = provider_factory()
    # Pad the user text so the turn clears sync_min_chars on its own.
    provider.sync_turn(USER_TEXT, assistant)
    assert _turn_files(provider) == []


def test_dream_report_wrapper_is_skipped(provider_factory):
    provider = provider_factory()
    report = (
        "- memórias salvas: nenhuma.\n"
        "- duplicatas ignoradas: a regra já está registrada no MemPalace.\n"
        "- alertas: nada novo que mereça memória longa."
    )
    provider.sync_turn(USER_TEXT, report)
    assert _turn_files(provider) == []


def test_cleanup_report_wrapper_is_skipped(provider_factory):
    provider = provider_factory()
    report = (
        "- removi 2 entradas obsoletas\n"
        "- traduzi 1 entrada para português\n"
        "- compactei 3 frases longas\n"
        "- eliminei duplicação de preferência de idioma"
    )
    provider.sync_turn("Execute a limpeza da memória curta.", report)
    assert _turn_files(provider) == []


def test_rendered_dream_report_heading_is_skipped(provider_factory):
    provider = provider_factory()
    report = (
        "# MemPalace Dream Report\n\n- Remembered: 0\n- Duplicates: 3\n"
        "- Rejected: 5\n\n## Remembered\n\n_None_"
    )
    provider.sync_turn(USER_TEXT, report)
    assert _turn_files(provider) == []


def test_material_turn_mentioning_silent_is_kept(provider_factory):
    provider = provider_factory()
    assistant = (
        "O modo [SILENT] do cron suprime a entrega quando não há nada novo; "
        "expliquei ao Mestre como configurar isso no prompt do job."
    )
    provider.sync_turn(USER_TEXT, assistant)
    assert len(_turn_files(provider)) == 1


def test_low_value_filter_is_configurable_off(provider_factory):
    provider = provider_factory(sync_skip_low_value=False)
    provider.sync_turn(USER_TEXT, "Sem novos fatos duráveis hoje.")
    assert len(_turn_files(provider)) == 1


# ---------------------------------------------------------------------------
# Normalized content dedup across sessions
# ---------------------------------------------------------------------------


def test_same_content_across_sessions_is_filed_once(provider_factory, tmp_path):
    corpus = tmp_path / "shared-corpus"
    first = provider_factory(session_id="20260701_010101_aaaa", corpus=corpus)
    second = provider_factory(session_id="20260702_020202_bbbb", corpus=corpus)

    first.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    second.sync_turn(USER_TEXT, ASSISTANT_TEXT)

    assert len(_turn_files(first)) == 1
    assert second._mine_calls == 0


def test_whitespace_and_case_variants_are_deduplicated(provider_factory, tmp_path):
    corpus = tmp_path / "shared-corpus"
    first = provider_factory(session_id="20260701_010101_aaaa", corpus=corpus)
    second = provider_factory(session_id="20260702_020202_bbbb", corpus=corpus)

    first.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    second.sync_turn(
        "  " + USER_TEXT.upper() + "  ",
        ASSISTANT_TEXT.replace(" ", "\n"),
    )

    assert len(_turn_files(first)) == 1


def test_materially_different_turns_are_preserved(provider_factory, tmp_path):
    corpus = tmp_path / "shared-corpus"
    first = provider_factory(session_id="20260701_010101_aaaa", corpus=corpus)
    second = provider_factory(session_id="20260702_020202_bbbb", corpus=corpus)

    first.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    second.sync_turn(
        USER_TEXT,
        "Decidimos migrar o gateway para gRPC em vez de filas, revertendo "
        "a decisão anterior após os testes de carga da semana.",
    )

    assert len(_turn_files(first)) == 2


def test_same_session_repeat_is_still_deduplicated(provider_factory):
    provider = provider_factory()
    provider.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    provider.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    assert len(_turn_files(provider)) == 1


def test_dedup_claim_is_atomic_for_concurrent_processes(provider_factory, tmp_path):
    # Simulate the cross-process race: the second writer finds the marker
    # already claimed and must skip without writing a transcript.
    corpus = tmp_path / "shared-corpus"
    provider = provider_factory(corpus=corpus)
    module = provider_factory.module

    digest = provider._normalized_turn_digest(USER_TEXT, ASSISTANT_TEXT)
    assert provider._claim_content_marker(digest) is True
    assert provider._claim_content_marker(digest) is False

    other = provider_factory(session_id="20260709_999999_ffff", corpus=corpus)
    other.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    assert _turn_files(other) == []
    assert isinstance(module, object)


def test_dedup_does_not_scan_turn_files(provider_factory, monkeypatch):
    # Dedup must be O(1) marker lookups, never a glob over the turns dir.
    provider = provider_factory()

    def _boom(self, *args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("sync_turn must not glob the corpus")

    monkeypatch.setattr(Path, "glob", _boom)
    provider.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    monkeypatch.undo()
    assert len(_turn_files(provider)) == 1


def test_failed_transcript_write_releases_the_marker(provider_factory, monkeypatch):
    provider = provider_factory()
    original_write_text = Path.write_text
    calls = {"n": 0}

    def _fail_once(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _fail_once)
    provider.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    assert _turn_files(provider) == []

    # The content must not be permanently lost: a retry succeeds.
    provider.sync_turn(USER_TEXT, ASSISTANT_TEXT)
    assert len(_turn_files(provider)) == 1
