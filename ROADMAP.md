# Roadmap

## 0. Production-ready bootstrap v1.0 — COMPLETE

This milestone is done. The repository ships a production-ready *bootstrap
and orchestration layer*: explicit apply, explicit opt-in cron creation, and
explicit opt-in post-apply verification, all dependency-injected and covered
by unit + integration-style tests against an isolated fake Hermes home.

It now includes an explicit MemPalace provider bootstrap path in setup:
bundled provider files + `uv tool install --upgrade mempalace`, both behind
the explicit `--install-provider` flag (see section 1).

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
    secrets redacted; no memory/cron/Obsidian/file writes);
  - `hermes mempalace-dreaming doctor` (read-only operational audit: plugin
    presence, memory provider, config coherence, cron state, duplicate and
    timezone-drift detection; never mutates anything). **Delivered.**
  - `hermes mempalace-dreaming repair-plan` (report-only repair plan derived
    from `doctor` findings: priority-ordered `repairs` with non-executed
    `command_preview` strings; no auto-fix, never invents a cron job id,
    never mutates anything). **Delivered.**
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
- memory writing (no memory writes during setup);
- Obsidian writes;
- vendoring third-party skills with unclear license.

## 1. Provider strategy — DELIVERED FOR THE SAFE BOOTSTRAP PATH

Chosen path:

1. Ship a minimal provider bundle in this repo for the safe bootstrap path.
2. Keep the actual setup mutation explicit and opt-in via `--install-provider`.
3. Keep unknown backend behavior report-only; never invent a fallback.

Acceptance criteria:

- `hermes memory status` reports `provider: mempalace`.
- Plugin can detect whether MemPalace tools are available.
- `setup --apply --install-provider` copies the bundled provider into
  `$HERMES_HOME/plugins/mempalace/` and installs `mempalace` via the explicit
  `--install-method` strategy (`auto|uv|pipx|pip-user`).
- Unknown backend remains report-only, not built-in fallback.

Delivered in the repo:

- `verify-runtime` parses `hermes memory status` read-only and reports whether
  the provider looks like `mempalace`.
- `setup-plan` / `setup` can now include a provider bootstrap plan when
  `--install-provider` is requested.
- `setup --apply --install-provider` copies the bundled provider files and
  runs the CLI install argv explicitly; failures are reported in JSON and gate
  later cron/verification steps.

Still environment-specific:

- whether at least one install path (`uv`, `pipx`, or `pip-user`) is present
  and allowed on the target machine;
- behavior against a real live-backend / gateway deployment path. Fresh/fake
  isolated-home validation is already covered in tests and was also hardened
  further by real runtime validation on `main`.

## 2. Dreaming engine

**v0.1 done:** `mempalace_dreaming/engine.py` ships a pure, dependency-free
pipeline (`mine_candidates` → `score_candidate` → `filter_durable_candidates`
→ `dedupe_candidates` → `remember_candidates`, plus `run_light_dream`
returning a `DreamReport`), `render_report()` (deterministic markdown) and
`audit_retrieval_noise()` (useful/noisy classification, no memory writes).
`search_fn`/`remember_fn` are injected; no Hermes tools are imported.
Covered by `tests/test_engine.py`.

**REM-style report-only integration: done.**
`build_integration_report(memories)` adds a pure, deterministic, side-effect
free analysis exposed via the `integration-report` CLI command: it reports
`contradictions` (opposing polarity / antonym pairs on a shared topic),
`supersede_candidates` (recency/override marker on one of two same-topic
memories) and `clusters` (identical deterministic topic key, size ≥ 2).
Secret-like / temporary entries are excluded and never echoed. It only
*reports*: it never reads, writes, deletes, or supersedes memory. Covered by
`tests/test_integration_report.py`.

Still external by design: wiring the integration analysis to a **live**
MemPalace provider corpus (vs. supplied input) — the conservative resolution
of contradictions/supersedes stays manual and report-first, never automatic.

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
  - `plugins.mempalace_dreaming.skill mempalace-dreaming:mempalace-dreaming`
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

Fresh-repo coverage now includes:

- a deterministic provider install-method strategy (`auto|uv|pipx|pip-user`)
  with ordered fallback and honest rollback notes;
- a fresh/fake `$HERMES_HOME` smoke in the test suite covering dry-run + apply
  without a real Hermes runtime.

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

**Weekly live-provider lean-check cron: done.** `setup
--schedule-lean-check` adds a report-only weekly schedule block;
`setup --apply --create-lean-check-cron` creates a deterministic, **distinct**
named weekly job (`mempalace-dreaming-weekly-lean-check`) via the same real
`hermes cron create` contract and injected `schedule_fn`, `--deliver local`.
Its prompt explicitly queries the **live** MemPalace backend read-only and is
strictly report-only: it never deletes, compacts, rewrites, or persists
memory and proposes cleanup only for explicit human approval. `--time` /
`--lean-check-weekday` / `--timezone` are converted to a weekly UTC cron
(day-of-week shifted if the conversion crosses midnight). Same early-failure
gating as the daily cron. Covered by `tests/test_weekly_lean_check.py`.

Still future work:

- the weekly cron *prompt* targets a live provider, but executing it against
  a real MemPalace backend is a runtime/deployment concern, not repo code;
- manual cleanup only after user approval (unchanged: never automated).

## 5. CI and packaging

**Done.** `.github/workflows/test.yml` runs on push/PR with a 3.11 + 3.12
matrix and now has explicit, ordered validation steps before the suite:

- `Validate plugin.yaml` — name/version/`hooks == []`;
- `Validate skill frontmatter` — name/version of `SKILL.md`;
- `Import smoke` — imports `mempalace_dreaming.engine` / `.setup` and asserts
  the new public surface (`build_integration_report`,
  `build_lean_check_cron_argv`, `LEAN_CHECK_JOB_NAME`);
- `Run tests` — `python -m pytest tests -q`.

These mirror `tests/test_packaging.py` (kept) and are asserted by
`tests/test_ci_workflow.py`. Deliberately minimal — no release pipeline,
no PyPI publish.

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

- behaviour against a live MemPalace backend and the target gateway mode;
- whether the target machine actually has at least one working package-install
  path among `uv`, `pipx`, or `pip-user`.
