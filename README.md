# Hermes MemPalace Dreamer

[![tests](https://github.com/fagnersouza666/hermes-mempalace-dreamer/actions/workflows/test.yml/badge.svg)](https://github.com/fagnersouza666/hermes-mempalace-dreamer/actions/workflows/test.yml)

MemPalace-first dreaming and memory hygiene bundle for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

This repository is an early public MVP. It does **not** install or mutate a real Hermes configuration yet. It ships the first safe scaffold for a plugin whose job is to make Hermes memory consolidation use MemPalace as the primary semantic memory layer instead of bloating built-in `MEMORY.md` / `USER.md`.

## What it does today

Current implemented pieces:

- Hermes plugin metadata in `plugin.yaml`.
- Plugin entrypoint in `__init__.py`.
- Registers a plugin-provided skill:
  - `skills/mempalace-dreaming/SKILL.md`
- Registers a CLI command:
  - `hermes mempalace-dreaming setup-plan`
- Provides a dry-run setup planner:
  - `build_setup_plan(...)`
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

The setup command currently prints a JSON plan. It intentionally does **not** change `~/.hermes/config.yaml`, create cron jobs, install MemPalace, or write memories.

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

- No automatic config mutation.
- No automatic cron creation.
- No Obsidian writes.
- No built-in memory fallback for normal durable facts.
- Unknown backend fallback is report-only.
- Memory deletion/compaction must be explicit and user-approved.

## Status

MVP scaffold: usable as a design/test base, not production-ready yet.

See [`ROADMAP.md`](ROADMAP.md) for the next implementation steps.
