# Hermes MemPalace Dreamer

**English** · [Português do Brasil](README.pt-BR.md)

[![tests](https://github.com/fagnersouza666/hermes-mempalace-dreamer/actions/workflows/test.yml/badge.svg)](https://github.com/fagnersouza666/hermes-mempalace-dreamer/actions/workflows/test.yml)

MemPalace-first dreaming and memory hygiene bundle for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

**Public MVP v0.1 is complete.** This is an honest, safe MVP — not a
production-ready system. It ships a working safe surface (setup planning,
explicit opt-in apply, read-only status, report-only schedule plan) and a
pure, dependency-free dreaming engine. It does **not** install MemPalace,
create cron jobs, write to Obsidian, or write any memory. Production
readiness (see [`ROADMAP.md`](ROADMAP.md)) remains future work.

Its job is to make Hermes memory consolidation use MemPalace as the primary semantic memory layer instead of bloating built-in `MEMORY.md` / `USER.md`.

## What it does today

Current implemented pieces:

- Hermes plugin metadata in `plugin.yaml`.
- Plugin entrypoint in `__init__.py`.
- Registers a plugin-provided skill:
  - `skills/mempalace-dreaming/SKILL.md`
- Registers CLI commands:
  - `hermes mempalace-dreaming setup-plan` (always report-only)
  - `hermes mempalace-dreaming setup` (dry-run by default, `--apply` opt-in)
  - `hermes mempalace-dreaming status` (read-only JSON: version, modules, safety flags)
  - `hermes mempalace-dreaming verify-runtime` (live read-only environment check; no side effects)
  - `hermes mempalace-dreaming schedule-plan` (report-only JSON; never creates cron)
- Provides a dry-run setup planner:
  - `build_setup_plan(...)`
- Provides an explicit apply layer:
  - `mempalace_dreaming/setup.py` (`build_config_commands`, `apply_setup_plan`);
  - directory creation and `hermes config set ...` run only with `--apply`;
  - side effects are dependency-injected (`mkdir_fn` / `run_fn`) and unit-tested;
  - config commands are argv lists, run via `subprocess` without a shell;
  - schedule stays planned/report-only — **no real cron is created yet**;
  - apply never raises: the first failing action is caught, stops further
    actions, and is reported in the result's `errors` list (also in CLI JSON);
  - rollback notes are included in the result (printed for every run).
- Ships a pure, dependency-free dreaming engine MVP:
  - `mempalace_dreaming/engine.py` (mine → score → filter → dedupe → remember);
  - testable without the Hermes runtime; `search_fn`/`remember_fn` are injected;
  - rejects temporary/progress content and secrets, keeps durable facts;
  - `render_report(report)` → deterministic markdown summary;
  - `audit_retrieval_noise(results)` → pure useful/noisy classification (no memory writes).
- Includes tests for:
  - plugin registration;
  - skill contract;
  - setup plan contents;
  - CLI JSON output;
  - dreaming engine behavior.

`setup-plan` only prints a JSON plan. `setup` defaults to the same dry-run
JSON; with the explicit `--apply` flag it creates the planned directories and
runs `hermes config set ...` commands. If an action fails under `--apply`,
setup stops at the first failure and reports it in the JSON `errors` field
(directory creation failing means no config command runs); rollback notes are
always printed. Even with `--apply`, setup intentionally does **not** create
cron jobs, install MemPalace, write to Obsidian, or write any memories.

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

**Public MVP v0.1 — complete.** Safe setup planning, explicit opt-in apply,
read-only `status`, report-only `schedule-plan`, and a pure dreaming engine
are all implemented and tested. Usable as a design/test base.

A live, read-only `verify-runtime` check is now available — it reports on a
real Hermes install but never fixes, installs, or schedules anything.

**Not production-ready.** Provider installation and real cron scheduling
remain future work.

See [`docs/USAGE.md`](docs/USAGE.md) for commands and the safety model,
[`CHANGELOG.md`](CHANGELOG.md) for the v0.1.0 entry, and
[`ROADMAP.md`](ROADMAP.md) for what is and isn't done.
