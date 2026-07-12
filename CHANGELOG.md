# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/). This project uses
[Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-07-12

Memory-quality release: fixes the corpus pollution observed in production
(dozens of near-identical `turn-...-cron_*.md` transcripts filed by
scheduled cron runs) and reports the upstream cron-memory limitation
honestly instead of claiming cleanup succeeded.

### Fixed

- **provider: harden `sync_turn` against cron/background sessions.** Root
  cause: the Hermes core cron scheduler hardcodes `agent_context="primary"`
  for every session (upstream NousResearch/hermes-agent#9763), so the old
  `agent_context`-only guard filed every cron run — including the full
  skill text embedded in each cron prompt — into the corpus, day after
  day. The bundled provider now also honors the reported platform
  (`sync_skip_platforms`, default `[cron]`), the session-id prefix
  (`sync_skip_session_prefixes`, default `[cron_]`), and the cron delivery
  wrapper embedded in the user content as independent signals. Primary
  Telegram/CLI ingestion is unaffected.
- **provider: drop low-value maintenance/report turns.** `[SILENT]`,
  "Sem novos fatos duráveis", "Sem limpeza segura na memória curta",
  "no new durable facts", the deterministic `# MemPalace Dream Report`
  rendering, dreaming-report bullet wrappers (`- memórias salvas: ...`)
  and cleanup-report bullet wrappers (`- removi/traduzi/compactei/...`)
  are no longer filed (configurable via `sync_skip_low_value`). Material
  turns that merely mention these markers are preserved.
- **provider: cross-session normalized-content dedup.** Turns are now
  deduplicated by a digest of the normalized content (casefold +
  whitespace collapse), not per `(session, content)`, so the same content
  under a different session id is filed exactly once. The dedup index is
  an O(1) atomic marker directory (`<corpus>/turns/.dedup-index/`, claimed
  with `open(..., 'x')`), safe under concurrent processes, with no corpus
  scan per turn. A failed transcript write releases its marker so content
  is never permanently lost. Materially different turns are always
  preserved. Configurable via `sync_dedup_enabled`.
- honor the active Hermes home in live runtime commands and audits. Runtime
  validation against a fresh isolated profile exposed that `verify-runtime`,
  `doctor`, and `repair-plan` could drift back to `~/.hermes` if callers
  relied on `HERMES_HOME`; the commands now resolve the active Hermes home via
  the host helper when available, otherwise via `HERMES_HOME`, and only then
  fall back to the conventional default.
- exclude the distinct weekly lean-check cron
  (`mempalace-dreaming-weekly-lean-check`) from the daily dreaming duplicate
  detector in `doctor`. A valid install with both the daily cron and the
  weekly live-provider lean-check cron no longer raises a spurious duplicate
  dreaming-job warning.

### Added

- **`corpus-cleanup` command** to migrate an already-polluted corpus:
  read-only dry-run plan by default (classifies `background-session`,
  `low-value` and `duplicate-content` turn files, always keeping the
  earliest copy and anything unparseable); `--apply` MOVES the planned
  files into a backup directory (`--backup-dir`, default
  `<corpus>/cleanup-backup-<UTC stamp>/`) — it never deletes — and
  rebuilds the provider-compatible dedup index from the kept files. The
  palace is never read or written; re-mining after review is a deliberate
  manual step.
- **doctor/repair-plan/setup: detect the unsupported built-in
  short-memory cleanup cron (#9763).** `doctor` reads
  `$HERMES_HOME/cron/jobs.json` (read-only) and flags enabled agent jobs
  whose prompt targets built-in short-memory cleanup (memória curta /
  MEMORY.md / USER.md / `memory(action=...)`): Hermes cron sessions run
  with `skip_memory=True`, so the memory tool is unavailable and such a
  job reports "ok" without cleaning anything. The finding flips `doctor`
  to `ok=false`, `repair-plan` proposes a manual, report-only remediation
  (pause/remove the job by id, run the cleanup interactively), and the
  setup plan documents the limitation. Hermes core is never patched.
- document the post-`1.0.1` production hardening that is already present on
  `main`: explicit provider bootstrap during setup, deterministic install
  fallback (`auto|uv|pipx|pip-user`), fresh/fake Hermes-home smoke coverage,
  and the runtime-validation fixes above.

## [1.0.1] - 2026-05-17

### Added

- add installation doctor command (read-only operational audit: plugin/memory/config/cron, duplicate & timezone drift detection).
- add `repair-plan` command: report-only, JSON-only translation of doctor
  findings into a priority-ordered `repairs` list (id/priority/kind/reason/
  suggested_action/command_preview). Applies nothing — every command is a
  preview, never executed; never invents a cron job id.

### Fixed

- correct the bundled skill reference from `plugin:mempalace-dreaming` to the
  fully-qualified `mempalace-dreaming:mempalace-dreaming` form expected by the
  real Hermes runtime. Propagated consistently across the setup plan, the
  applied config keys (`plugins.mempalace_dreaming.skill`), the schedule/cron
  skill attachment, the `doctor` expected-config map, the `repair-plan` config
  preview literals, `docs/USAGE.md`, and the test suite — so bootstrap,
  config, cron, doctor and repair-plan all agree on the same skill id and a
  freshly applied environment passes its own `doctor`/`repair-plan` audit
  instead of reporting a spurious skill-config drift.
- harden `doctor` import fallback: `build_doctor_report()` no longer crashes
  with `ModuleNotFoundError: No module named 'mempalace_dreaming'` in the
  installed-plugin context. It now resolves `SCHEDULE_JOB_NAME` via the same
  plugin-local loading strategy used by `setup`/`apply`/`lean-check`, and
  reports the setup module as a JSON warning instead of raising when it is
  genuinely unavailable.

## [1.0.0] - 2026-05-16

Production-ready bootstrap v1.0. The plugin is now a safe bootstrap and
orchestration layer for environments that **already have a Hermes MemPalace
provider available**. Installing the provider package itself stays external
and out of scope by design.

### Added

- Explicit, dependency-injected cron creation in `setup --apply`:
  - new `--create-cron` flag (only acts with `--apply`);
  - `build_cron_create_argv()` builds a deterministic, argv-only
    `hermes cron create` command matching the real CLI contract —
    `--name`/`--deliver`/`--skill` options plus the cron expression and the
    conservative prompt as **positional** arguments (no invented
    `--schedule`/`--prompt` flags);
  - fixed job name (`mempalace-dreaming-daily`), `--deliver local` so a
    schedule never broadcasts to chats, conservative self-contained prompt,
    bundled skill attached;
  - failures are captured into the JSON `cron` result, never raised;
  - without `--create-cron`, scheduling stays report-only.
- Explicit, dependency-injected post-apply verification:
  - new `--verify-after-apply` flag (only acts with `--apply`);
  - runs the read-only runtime check via an injected `verify_fn` and embeds
    the report under `verification` in the JSON;
  - skipped (recorded with a reason) if apply failed early — never verifies
    a half-applied environment.
- Integration-style tests against an isolated fake Hermes home covering
  apply without/with cron, verify-after-apply, cron-absent, verify-skip on
  failure, and side-effect-free dry-run (`tests/test_production_bootstrap.py`).

### Changed

- Plugin/skill/package version `0.1.0` → `1.0.0`; status moved from
  "public MVP v0.1" to "production-ready bootstrap v1.0".
- `status` JSON safety block now reports `cron_creation_explicit` and
  `verify_after_apply_explicit`.
- Rollback notes describe accurate cron removal (list jobs, remove by job
  id) instead of an incorrect delete-by-name command.

### Safety

- No cron creation without explicit `--apply --create-cron`.
- No post-apply verification without explicit `--verify-after-apply`.
- No MemPalace provider package installation (external/pre-existing).
- No Obsidian writes; no memory writes during setup or verification.
- No hidden side effects at import/register time.

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
