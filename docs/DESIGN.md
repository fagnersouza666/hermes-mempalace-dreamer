# Design: Hermes MemPalace Dreamer

This document reflects the **current design** of the repository, not the old
MVP snapshot.

## Synthesis from existing projects

### Pluton ideas to reuse

- Dream cycle as a pipeline: `MINE -> CURATE -> COMPRESS -> CONTEXT`.
- Multiple analytical lenses, not just fact extraction.
- Threshold/schedule based consolidation.
- Testable separation between mechanics and LLM judgment.

Adaptation: replace Pluton's wiki-as-primary-memory with MemPalace semantic
storage and report-first structural recommendations.

### hermes-memory-skills ideas to reuse

- Light/Deep/REM modes.
- Lean-check as memory hygiene discipline.
- Skill-first operating instructions for agents.

Adaptation: remove built-in/Holographic assumptions. Unknown backend is
report-only. Normal durable facts go to MemPalace.

### Hermes MemPalace provider ideas to reuse

- Native `MemoryProvider` integration.
- Session-end diary and pre-compression distillation.
- Semantic recall and KG queries.

Adaptation: this repo now ships a **bundled safe provider bootstrap path** for
Hermes profiles: explicit provider-copy + explicit CLI install strategy,
behind opt-in setup flags.

## Current implemented surface

The repository now ships:

- plugin metadata and entrypoint;
- plugin-registered skill;
- report-only setup planning;
- explicit opt-in setup apply;
- explicit opt-in provider bootstrap;
- explicit opt-in daily dreaming cron creation;
- explicit opt-in weekly live-provider lean-check cron creation;
- read-only `status`, `verify-runtime`, `doctor`, and `repair-plan`;
- pure report-first dreaming / lean-check / integration helpers;
- unit and integration-style tests, including isolated fresh/fake Hermes-home
  flows.

## Setup / bootstrap architecture

### 1. Planning first

`setup-plan` and dry-run `setup` always describe the intended mutations before
anything is applied.

### 2. Explicit apply only

Real mutation happens only under explicit flags such as:

- `--apply`
- `--install-provider`
- `--create-cron`
- `--create-lean-check-cron`
- `--verify-after-apply`

No side effect happens at import/register time.

### 3. Provider bootstrap

The repo bundles profile-safe provider artifacts that are copied into
`$HERMES_HOME/plugins/mempalace/` only when the operator explicitly requests
`setup --apply --install-provider`.

The CLI install path is argv-only and deterministic:

- `uv`
- `pipx`
- `pip-user`
- `auto` = `uv -> pipx -> pip-user`

The first successful method wins; every attempt is reported.

### 4. Runtime honesty

Runtime validation against a fresh isolated profile hardened two important
rules that the code now follows:

- runtime/audit commands must honor the **active** Hermes home, preferring the
  host helper and then `HERMES_HOME`, instead of silently drifting to
  `~/.hermes`;
- the weekly lean-check cron is a **distinct** operational job and must not be
  treated as a duplicate of the daily dreaming cron.

## Current boundaries

The current design intentionally still does **not**:

- write to Obsidian;
- write memories during setup, verification, doctor, or repair planning;
- auto-delete or auto-compact memory;
- vendor third-party upstream skills with unclear license;
- claim that live-backend or real gateway validation is solved purely inside
  this repo.

## Environment-specific concerns that remain external

These are still deployment/runtime concerns rather than repo-code gaps:

- validating against a *live* MemPalace backend with real data;
- validating long-running gateway behavior in the target environment;
- confirming which install path (`uv`, `pipx`, or `pip-user`) is available and
  acceptable on each target host.

## Safety policy

- No secrets stored.
- No Obsidian writes.
- No built-in memory fallback for normal facts.
- Cron automation stays conservative and report-first.
- Deletion/compaction requires explicit approval.
