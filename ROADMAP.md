# Roadmap

## 0. Production-ready bootstrap v1.0 — COMPLETE

This milestone is done. The repository ships a production-ready *bootstrap
and orchestration layer*: explicit apply, explicit opt-in cron creation, and
explicit opt-in post-apply verification, all dependency-injected and covered
by unit + integration-style tests against an isolated fake Hermes home.

It assumes a Hermes MemPalace provider is **already available** in the
environment. Installing that provider package itself is external and
intentionally out of scope (see section 1).

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
  - `hermes mempalace-dreaming schedule-plan` (report-only JSON; no cron);
  - `hermes mempalace-dreaming lean-check` (report-only JSON; local
    `--input-file`/`--json-input`; classifies durable/noisy/secret/duplicate;
    secrets redacted; no memory/cron/Obsidian/file writes).
- Pure engine reporting/audit surface:
  - `render_report(report)` — deterministic markdown summary;
  - `audit_retrieval_noise(results)` — useful/noisy classification, no writes;
  - `build_lean_check_report(candidates, search_fn=…)` — report-only
    classification (durable/noisy/secret/duplicate), deterministic, no writes.
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

Now implemented (v1.0):

- explicit opt-in cron creation via `--apply --create-cron` — deterministic
  `hermes cron create` argv matching the real CLI (positional schedule +
  prompt, no invented flags), fixed job name, `--deliver local`, conservative
  prompt, injected `schedule_fn`, failures captured not raised;
- explicit opt-in post-apply verification via `--apply --verify-after-apply`
  — read-only, injected `verify_fn`, embedded in JSON, skipped if apply
  failed early.

Still external/out of scope by design:

- installing the `mempalace` provider package itself (environment-specific).

## 4. Cron routines

**Partially addressed.** `lean-check` exists today as a **report-only,
local-input-based** command (`--input-file` / `--json-input`) — it does not
query a live MemPalace backend and creates no schedule. Duplicate detection
is available only via an injected `search_fn` in the pure helper.

**Daily dreaming cron: done (v1.0).** `setup --apply --create-cron` creates
a deterministic, named daily dreaming job via the real `hermes cron create`
contract through an injected `schedule_fn`, `--deliver local`, conservative
prompt, bundled skill attached. Rollback guidance points at
`hermes cron list` + remove-by-job-id.

Still future work:

- weekly lean-check report wired to a live provider (not local input);
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

Done for the bootstrap layer (v1.0):

- explicit apply, cron creation, and post-apply verification, all
  dependency-injected and tested without a real Hermes;
- integration-style tests against an isolated fake Hermes home;
- cron argv verified against the real `hermes cron create` contract
  (positional schedule + prompt; no invented flags);
- no secret-shaped material baked into the cron prompt (tested);
- no built-in memory pollution; no memory writes during setup/verification;
- rollback documented (config revert; cron via list + remove-by-job-id).

Still environment-specific (out of scope here, validate per deployment):

- installing the MemPalace provider package itself;
- behaviour against a specific fresh Hermes install and gateway mode.
