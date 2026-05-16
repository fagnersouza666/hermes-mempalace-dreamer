# Roadmap

## 0. Public MVP v0.1 — COMPLETE

This milestone is done. The repository ships a complete, honest, safe public
MVP. It is **not** production-ready (see section 7); that is intentional and
future work.

Already implemented:

- Plugin metadata in `plugin.yaml`.
- Hermes plugin entrypoint in `__init__.py`.
- Plugin-provided skill in `skills/mempalace-dreaming/SKILL.md`.
- CLI commands:
  - `hermes mempalace-dreaming setup-plan` (always report-only);
  - `hermes mempalace-dreaming setup` (dry-run by default, explicit `--apply`);
  - `hermes mempalace-dreaming status` (read-only JSON; no memory calls);
  - `hermes mempalace-dreaming verify-runtime` (live read-only environment
    check; runs `hermes --version` / `hermes memory status` captured, detects
    the memory provider, checks bundled skill/modules/dirs; mutates nothing);
  - `hermes mempalace-dreaming schedule-plan` (report-only JSON; no cron).
- Pure engine reporting/audit surface:
  - `render_report(report)` — deterministic markdown summary;
  - `audit_retrieval_noise(results)` — useful/noisy classification, no writes.
- Packaging metadata: `pyproject.toml` `[build-system]`, validated by tests.
- Pure setup planner:
  - `build_setup_plan(...)`
- Explicit setup apply layer:
  - `mempalace_dreaming/setup.py` (`build_config_commands`, `apply_setup_plan`);
  - injected `mkdir_fn` / `run_fn`; argv-list config commands (no shell);
  - dry-run never touches the filesystem or runs Hermes;
  - `--apply` creates directories and runs `hermes config set ...`;
  - rollback notes included.
- Tests for:
  - plugin registration;
  - setup plan contents;
  - CLI JSON output (`setup-plan` and `setup`);
  - apply layer (dry-run vs. apply, schedule report-only);
  - MemPalace-first skill policy.

Deliberately not implemented yet:

- config mutation without explicit `--apply` (default stays dry-run);
- MemPalace package installation;
- provider installation;
- cron creation (apply mode still does not create cron);
- memory writing (no memory writes during setup);
- Obsidian writes;
- vendoring third-party skills with unclear license.

## 1. Provider strategy

Choose one path:

1. Depend on an existing Hermes MemPalace provider plugin and configure it.
2. Vendor/adapt a provider only if the license is explicit and compatible.
3. Ship a minimal provider in this repo.

Recommendation: start with dependency/configuration, not vendoring. Less code, less legal mud.

Acceptance criteria:

- `hermes memory status` reports `provider: mempalace`.
- Plugin can detect whether MemPalace tools are available.
- Unknown backend remains report-only, not built-in fallback.

Partially addressed: `verify-runtime` already parses `hermes memory status`
read-only and reports whether the provider looks like `mempalace` (warning,
never a fallback or mutation). It does not yet install or configure a
provider — that is still future work below.

## 2. Dreaming engine

**v0.1 done:** `mempalace_dreaming/engine.py` ships a pure, dependency-free
pipeline (`mine_candidates` → `score_candidate` → `filter_durable_candidates`
→ `dedupe_candidates` → `remember_candidates`, plus `run_light_dream`
returning a `DreamReport`), `render_report()` (deterministic markdown) and
`audit_retrieval_noise()` (useful/noisy classification, no memory writes).
`search_fn`/`remember_fn` are injected; no Hermes tools are imported.
Covered by `tests/test_engine.py`.

Future engine work (post-v0.1): deeper REM-style integration (contradiction
resolution, supersede detection, semantic clustering) wired to a live
MemPalace provider.

Rules:

- Memory reads/writes are dependency-injected for tests.
- `mempalace_search` happens before `mempalace_remember`.
- Temporary task progress, SHAs, issue numbers, PR numbers, logs, and secrets are rejected.
- Skills are suggested, not auto-created, during cron runs.

## 3. Installer / setup apply mode

**Done:** explicit apply mode shipped.

```bash
hermes mempalace-dreaming setup            # dry-run JSON
hermes mempalace-dreaming setup --apply    # create dirs + set config
```

Implemented:

- `setup` defaults to dry-run; `--apply` is explicit;
- `--apply` creates planned directories;
- `--apply` sets Hermes config keys via argv-list `hermes config set ...`:
  - `memory.memory_enabled true`
  - `memory.user_profile_enabled true`
  - `memory.provider mempalace`
  - `plugins.mempalace_dreaming.enabled true`
  - `plugins.mempalace_dreaming.skill plugin:mempalace-dreaming`
- rollback notes printed in the JSON result;
- no destructive cleanup, no Obsidian writes, no memory writes.

Still not implemented (tracked here, intentionally):

- install or verify the `mempalace` package;
- verify `hermes memory status` after apply;
- creating a real daily dreaming cron (schedule stays report-only).

## 4. Cron routines

Add optional scheduling helpers:

```bash
hermes mempalace-dreaming schedule --time 05:30
hermes mempalace-dreaming lean-check --weekly
```

Planned jobs:

- daily light dreaming;
- weekly lean-check report;
- manual cleanup only after user approval.

## 5. CI and packaging

Add GitHub Actions:

- run tests on Python 3.11 and 3.12;
- validate `plugin.yaml`;
- validate skill frontmatter;
- run a minimal import test.

Packaging goals:

- support `hermes plugins install fagnersouza666/hermes-mempalace-dreamer --enable`;
- keep plugin installable directly from GitHub;
- later decide whether PyPI packaging is useful.

## 6. Attribution and licensing

Before vendoring any external content:

- confirm license for `nexus9888/hermes-memory-skills`;
- attribute Pluton and other inspirations in docs;
- do not copy upstream skill text unless permission/license is clear.

Current policy: clean-room adaptation only.

## 7. Production readiness

Before calling this production-ready:

- test against a fresh Hermes install;
- test with MemPalace enabled and disabled;
- test cron behavior in gateway mode;
- verify no secret leakage in reports;
- verify no built-in memory pollution;
- document rollback.
