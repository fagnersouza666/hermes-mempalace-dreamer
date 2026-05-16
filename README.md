# Hermes MemPalace Dreamer

**English** · [Português do Brasil](README.pt-BR.md)

[![tests](https://github.com/fagnersouza666/hermes-mempalace-dreamer/actions/workflows/test.yml/badge.svg)](https://github.com/fagnersouza666/hermes-mempalace-dreamer/actions/workflows/test.yml)

MemPalace-first dreaming and memory hygiene bundle for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

This repository is an early public MVP. It does **not** install or mutate a real Hermes configuration yet. It ships the first safe scaffold for a plugin whose job is to make Hermes memory consolidation use MemPalace as the primary semantic memory layer instead of bloating built-in `MEMORY.md` / `USER.md`.

## What it does today

Current implemented pieces:

- Hermes plugin metadata in `plugin.yaml`.
- Plugin entrypoint in `__init__.py`.
- Registers a plugin-provided skill:
  - `skills/mempalace-dreaming/SKILL.md`
- Registers CLI commands:
  - `hermes mempalace-dreaming setup-plan` (always report-only)
  - `hermes mempalace-dreaming setup` (dry-run by default, `--apply` opt-in)
- Provides a dry-run setup planner:
  - `build_setup_plan(...)`
- Provides an explicit apply layer:
  - `mempalace_dreaming/setup.py` (`build_config_commands`, `apply_setup_plan`);
  - directory creation and `hermes config set ...` run only with `--apply`;
  - side effects are dependency-injected (`mkdir_fn` / `run_fn`) and unit-tested;
  - config commands are argv lists, run via `subprocess` without a shell;
  - schedule stays planned/report-only — **no real cron is created yet**;
  - rollback notes are included in the result.
- Ships a pure, dependency-free dreaming engine MVP:
  - `mempalace_dreaming/engine.py` (mine → score → filter → dedupe → remember);
  - testable without the Hermes runtime; `search_fn`/`remember_fn` are injected;
  - rejects temporary/progress content and secrets, keeps durable facts.
- Includes tests for:
  - plugin registration;
  - skill contract;
  - setup plan contents;
  - CLI JSON output;
  - dreaming engine behavior.

`setup-plan` only prints a JSON plan. `setup` defaults to the same dry-run
JSON; with the explicit `--apply` flag it creates the planned directories and
runs `hermes config set ...` commands. Even with `--apply`, setup intentionally
does **not** create cron jobs, install MemPalace, write to Obsidian, or write
any memories.

## Intended direction

The final component should become a one-install Hermes plugin for:

- MemPalace-first memory dreaming;
- memory hygiene / lean-check routines;
- optional daily dreaming cron;
- safe setup of `memory.provider: mempalace`;
- integration with a Hermes MemPalace provider;
- clean-room adaptation of ideas from existing memory-dreaming projects.

## Design inputs

This project borrows ideas, not code, from:

- Pluton: `MINE -> CURATE -> COMPRESS -> CONTEXT` dream pipeline.
- `nexus9888/hermes-memory-skills`: Light/Deep/REM structure and lean-check discipline.
- Hermes MemPalace provider plugins: native provider, prefetch, diary, and knowledge graph concepts.

No third-party skill text is vendored here until license and attribution are explicit.

## Install

Once published and supported by your Hermes version:

```bash
hermes plugins install fagnersouza666/hermes-mempalace-dreamer --enable
hermes mempalace-dreaming setup-plan --schedule-dreaming
```

For local development:

```bash
git clone https://github.com/fagnersouza666/hermes-mempalace-dreamer.git
cd hermes-mempalace-dreamer
python3 -m pytest tests -q
```

## Current safety policy

- No config mutation without the explicit `setup --apply` flag (default is dry-run).
- No automatic cron creation (apply mode still does not create cron).
- No Obsidian writes.
- No memory writes during setup.
- No built-in memory fallback for normal durable facts.
- Unknown backend fallback is report-only.
- Memory deletion/compaction must be explicit and user-approved.

## Status

MVP scaffold: usable as a design/test base, not production-ready yet.

See [`ROADMAP.md`](ROADMAP.md) for the next implementation steps.
