# Usage

Public MVP v0.1. Every command below is safe: nothing here installs
MemPalace, creates a cron job, writes to Obsidian, or writes any memory.

## Commands

All commands print JSON (except `setup --apply`, which performs the explicit
side effects and then prints a JSON result).

### `status` — read-only

```bash
hermes mempalace-dreaming status
```

Prints plugin name/version/status, whether the bundled skill and engine/setup
modules are present, and the safety flags. Makes no memory calls and mutates
nothing.

### `verify-runtime` — live, read-only

```bash
hermes mempalace-dreaming verify-runtime --hermes-home ~/.hermes
```

Probes the live environment and prints a JSON report. It is strictly
read-only: it runs `hermes --version` and `hermes memory status` (captured,
never raising), checks whether the memory provider looks like `mempalace`,
whether the bundled skill and `mempalace_dreaming.engine`/`.setup` modules
are present, and whether the expected `mempalace` directories for the chosen
`--hermes-home` exist. It mutates nothing — no config, memory, cron, or
files — and does **not** install a provider or create directories. The
payload always carries a top-level `ok` boolean and a `warnings` list; a
failed subprocess becomes a warning, not a crash. Command stdout is not
echoed into the JSON, to avoid leaking environment details.

This is the live counterpart to `status`, which stays purely local/static.

### `setup-plan` — report-only

```bash
hermes mempalace-dreaming setup-plan --hermes-home ~/.hermes --schedule-dreaming --time 05:30
```

Prints the setup plan (directories, `hermes config set ...` commands,
optional schedule). Never applies anything.

### `setup` — dry-run by default, opt-in apply

```bash
hermes mempalace-dreaming setup                 # dry-run JSON (no side effects)
hermes mempalace-dreaming setup --apply         # create dirs + run config commands
```

`--apply` creates the planned directories and runs `hermes config set ...`
(argv lists, no shell). The first failing action stops the rest and is
reported in the JSON `errors` field; rollback notes are always printed. Even
with `--apply`, setup never creates cron, installs MemPalace, writes to
Obsidian, or writes memory.

### `schedule-plan` — report-only

```bash
hermes mempalace-dreaming schedule-plan --time 05:30
```

Prints only a JSON schedule plan describing what a conservative daily
dreaming cron would look like. **No cron job is created.** Schedule it
yourself with your Hermes cron tooling if you want automation.

### `lean-check` — report-only

```bash
hermes mempalace-dreaming lean-check --input-file candidates.txt
hermes mempalace-dreaming lean-check --json-input '["User prefers tabs on Linux"]'
hermes mempalace-dreaming lean-check                 # no input -> valid JSON + warning
```

Classifies candidate memory/retrieval material into `durable`, `noisy`,
`secret`, and `duplicate` and prints a JSON report with counts, capped
example texts, `warnings`, and heuristic `recommendations` (e.g. "noisy
recall looks high", "duplicate rate looks high", a secret-found warning).

It is strictly **report-only and local-input-based** in the public MVP: it
reads `--input-file` (one candidate text per line) or `--json-input` (a JSON
array of strings or `{"text": ...}` objects) and writes nothing — no memory,
cron, config, Obsidian, or files. It does **not** query a live MemPalace
backend; duplicate detection only happens when a `search_fn` is injected into
the pure helper. Secret-like material is counted and warned about but its
text is **redacted** in the report, never echoed back. Missing input,
unreadable files, or invalid JSON become warnings, not a crash.

## Pure engine API

`mempalace_dreaming.engine` imports no Hermes runtime. Memory reads/writes
are dependency-injected (`search_fn` / `remember_fn`).

- `run_light_dream(entries, search_fn, remember_fn)` → `DreamReport`
- `render_report(report)` → deterministic markdown string
- `audit_retrieval_noise(results)` → `RetrievalAuditReport` (useful vs noisy;
  pure, never writes memory)
- `build_lean_check_report(candidates, search_fn=…, extra_warnings=…)` →
  report-only JSON dict (durable / noisy / secret / duplicate counts +
  redacted examples + warnings/recommendations; pure, no writes)

## Safety model

- No config mutation without the explicit `setup --apply` flag.
- No automatic cron creation (apply mode still does not create cron).
- No Obsidian writes.
- No memory writes during setup, `status`, or `schedule-plan`.
- No built-in memory fallback for normal durable facts.
- Unknown backend fallback is report-only.
- Memory deletion/compaction must be explicit and user-approved.
- No hidden side effects at import/register time.

## Not production-ready

`verify-runtime` adds a read-only check against a live Hermes install, but it
only *reports* — it never fixes, installs, or schedules anything. Provider
installation and real cron scheduling remain future work, and this is still
not a production-ready system. See [`../ROADMAP.md`](../ROADMAP.md).
