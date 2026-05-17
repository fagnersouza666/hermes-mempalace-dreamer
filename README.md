# Hermes MemPalace Dreamer

**English** · [Português do Brasil](README.pt-BR.md)

[![tests](https://github.com/fagnersouza666/hermes-mempalace-dreamer/actions/workflows/test.yml/badge.svg)](https://github.com/fagnersouza666/hermes-mempalace-dreamer/actions/workflows/test.yml)

MemPalace-first dreaming and memory hygiene bundle for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

**Production-ready bootstrap v1.0.** This is an honest, safe bootstrap and
orchestration layer for environments that **already have a Hermes MemPalace
provider available**. It ships a working safe surface (setup planning,
explicit opt-in apply, explicit opt-in cron creation, explicit opt-in
post-apply verification, read-only status/verify) and a pure,
dependency-free dreaming engine. It does **not** install the MemPalace
provider package itself — that remains external/pre-existing and
environment-specific. It never writes to Obsidian and never writes memory
during setup or verification. Every side effect is explicit and
dependency-injected.

Its job is to make Hermes memory consolidation use MemPalace as the primary semantic memory layer instead of bloating built-in `MEMORY.md` / `USER.md`.

## What it does today

Current implemented pieces:

- Hermes plugin metadata in `plugin.yaml`.
- Plugin entrypoint in `__init__.py`.
- Registers a plugin-provided skill:
  - `skills/mempalace-dreaming/SKILL.md`
- Registers CLI commands:
  - `hermes mempalace-dreaming setup-plan` (always report-only)
  - `hermes mempalace-dreaming setup` (dry-run by default; `--apply` opt-in;
    explicit `--create-cron` and `--verify-after-apply` opt-ins)
  - `hermes mempalace-dreaming status` (read-only JSON: version, modules, safety flags)
  - `hermes mempalace-dreaming verify-runtime` (live read-only environment check; no side effects)
  - `hermes mempalace-dreaming schedule-plan` (report-only JSON; never creates cron)
  - `hermes mempalace-dreaming lean-check` (report-only JSON; classifies local candidate material, no writes)
  - `hermes mempalace-dreaming doctor` (read-only operational audit: plugin presence, memory provider, config coherence, cron state, duplicate/timezone-drift detection; never mutates anything)
  - `hermes mempalace-dreaming repair-plan` (report-only: turns doctor findings into an explicit, priority-ordered repair plan with command previews; never applies any fix)
- Provides a dry-run setup planner:
  - `build_setup_plan(...)`
- Provides an explicit apply layer:
  - `mempalace_dreaming/setup.py` (`build_config_commands`, `apply_setup_plan`);
  - directory creation and `hermes config set ...` run only with `--apply`;
  - side effects are dependency-injected (`mkdir_fn` / `run_fn` /
    `schedule_fn` / `verify_fn`) and unit/integration-tested;
  - config and cron commands are argv lists, run via `subprocess` without a shell;
  - cron creation is **explicit and opt-in** (`--apply --create-cron`):
    deterministic `hermes cron create` argv, fixed job name, conservative
    self-contained prompt, bundled skill attached, `--deliver local` so it
    never broadcasts to chats; without `--create-cron` scheduling stays
    report-only;
  - cron scheduling is **timezone-aware**: `--time` is a wall-clock time
    interpreted in `--timezone` (an IANA name, e.g. `America/Sao_Paulo`) and
    converted to a UTC cron, because the scheduler runs cron in UTC. The
    default timezone is **UTC** — not "local time"; pass `--timezone`
    explicitly for local-time scheduling. Plan output shows both the
    requested time/timezone and the resulting UTC cron; an unknown timezone
    becomes a JSON warning, never a traceback;
  - post-apply verification is **explicit and opt-in**
    (`--apply --verify-after-apply`): a read-only runtime check whose report
    is embedded in the JSON; it is skipped if apply failed early;
  - apply never raises: the first failing action is caught, stops further
    actions, and is reported in the result's `errors` list (also in CLI JSON);
  - rollback notes are included in the result (printed for every run).
- Ships a pure, dependency-free dreaming engine MVP:
  - `mempalace_dreaming/engine.py` (mine → score → filter → dedupe → remember);
  - testable without the Hermes runtime; `search_fn`/`remember_fn` are injected;
  - rejects temporary/progress content and secrets, keeps durable facts;
  - `render_report(report)` → deterministic markdown summary;
  - `audit_retrieval_noise(results)` → pure useful/noisy classification (no memory writes);
  - `build_lean_check_report(candidates, search_fn=…)` → report-only JSON classifying candidate
    material into durable / noisy / secret / duplicate (secrets redacted, no writes).
- Includes tests for:
  - plugin registration;
  - skill contract;
  - setup plan contents;
  - CLI JSON output;
  - dreaming engine behavior.

`setup-plan` only prints a JSON plan. `setup` defaults to the same dry-run
JSON; with the explicit `--apply` flag it creates the planned directories and
runs `hermes config set ...` commands. Adding `--create-cron` (only with
`--apply`) creates the daily dreaming cron via injected `schedule_fn`;
adding `--verify-after-apply` runs the read-only runtime check afterwards
and embeds it in the JSON. If an action fails under `--apply`, setup stops
at the first failure and reports it in the JSON `errors` field (directory
creation failing means no config command runs); cron and verification are
then skipped, not silently attempted. Rollback notes are always printed.
Even with every flag set, setup intentionally does **not** install the
MemPalace provider package, write to Obsidian, or write any memories.

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
- No cron creation without the explicit `setup --apply --create-cron` flags.
- No post-apply verification without the explicit `--verify-after-apply` flag,
  and it is skipped if apply failed early.
- No Obsidian writes.
- No memory writes during setup or verification.
- No built-in memory fallback for normal durable facts.
- Unknown backend fallback is report-only.
- Memory deletion/compaction must be explicit and user-approved.

## Status

**Production-ready bootstrap v1.0.** Safe setup planning, explicit opt-in
apply, explicit opt-in cron creation, explicit opt-in post-apply
verification, read-only `status`/`verify-runtime`, and a pure dreaming
engine are all implemented and covered by unit + integration-style tests
(including end-to-end runs against an isolated fake Hermes home).

**Scope of "production-ready":** this is a production-ready *bootstrap and
orchestration layer*. It assumes a Hermes MemPalace provider is already
available in the environment — installing that provider package itself is
external and remains out of scope by design.

See [`docs/USAGE.md`](docs/USAGE.md) for commands and the safety model,
[`CHANGELOG.md`](CHANGELOG.md) for the v1.0.1 entry, and
[`ROADMAP.md`](ROADMAP.md) for what is and isn't done.
