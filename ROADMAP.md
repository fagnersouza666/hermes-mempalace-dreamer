# Roadmap

## 0. Current MVP

- Plugin metadata in `plugin.yaml`.
- Hermes plugin entrypoint in `__init__.py`.
- Plugin-provided skill in `skills/mempalace-dreaming/SKILL.md`.
- Dry-run CLI: `hermes mempalace-dreaming setup-plan` once installed as plugin.
- Tests for registration, setup plan, CLI JSON output, and skill policy.

## 1. Next implementation step

Choose provider strategy:

1. Depend on an existing MemPalace Hermes provider plugin and only configure it.
2. Vendor/adapt a provider with explicit MIT-compatible license.
3. Ship a minimal provider in this repo.

Recommendation: start with dependency/configuration, not vendoring. Less code, less legal mud.

## 2. Dreaming engine

Add a pure Python engine module with testable stages:

- `mine_sessions()`
- `score_candidates()`
- `dedupe_with_mempalace()`
- `remember_candidates()`
- `audit_retrieval_noise()`
- `render_report()`

All memory writes must be dependency-injected for testing.

## 3. Installer

Upgrade dry-run setup plan into explicit apply mode:

- install/verify `mempalace` package;
- set Hermes config keys;
- verify `hermes memory status`;
- optionally create daily dreaming cron;
- print rollback instructions.

No automatic destructive changes.

## 4. GitHub publication

Before publishing:

- decide repo name: `hermes-mempalace-dreamer` or `hermes-mempalace-memory`;
- add CI workflow;
- add install docs using `hermes plugins install OWNER/REPO --enable`;
- clarify attribution to Pluton, hermes-memory-skills, and MemPalace provider plugins;
- do not vendor `nexus9888/hermes-memory-skills` until license is explicit.
