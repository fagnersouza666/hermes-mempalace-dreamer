# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/). This project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-05-16

Public MVP v0.1 — complete, honest, and safe. Not production-ready;
production readiness remains future work (see `ROADMAP.md`).

### Added

- Hermes plugin metadata (`plugin.yaml`) and entrypoint (`__init__.py`) with
  no side effects at import/register time.
- Bundled MemPalace-first skill (`skills/mempalace-dreaming/SKILL.md`); no
  built-in memory fallback for normal durable facts.
- Pure dreaming engine (`mempalace_dreaming/engine.py`): mine → score →
  filter → dedupe → remember, `run_light_dream` → `DreamReport`;
  `search_fn`/`remember_fn` injected; rejects temporary/progress content and
  secrets.
- `render_report(report)` — deterministic markdown summary of a dream run.
- `audit_retrieval_noise(results)` → `RetrievalAuditReport` — pure
  useful/noisy classification; never writes memory.
- Explicit setup apply layer (`mempalace_dreaming/setup.py`):
  `build_config_commands`, `apply_setup_plan`; injected `mkdir_fn`/`run_fn`;
  argv-list config commands (no shell); first-failure-stops with reported
  `errors`; rollback notes.
- CLI commands: `setup-plan` (report-only), `setup` (dry-run default,
  explicit `--apply`), `status` (read-only JSON), `schedule-plan`
  (report-only JSON; no cron created).
- `pyproject.toml` `[build-system]` metadata (setuptools backend; no new
  runtime dependencies).
- `docs/USAGE.md` with commands and the safety model.
- Test suite covering engine, setup apply, CLI, plugin contract, packaging
  metadata, and CI workflow.

### Safety

- No config mutation without explicit `--apply`.
- No automatic cron creation (apply mode still does not create cron).
- No Obsidian writes; no memory writes during setup/status/schedule-plan.
- No hidden side effects at import/register time.
