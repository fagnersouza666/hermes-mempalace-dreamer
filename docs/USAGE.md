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

## Pure engine API

`mempalace_dreaming.engine` imports no Hermes runtime. Memory reads/writes
are dependency-injected (`search_fn` / `remember_fn`).

- `run_light_dream(entries, search_fn, remember_fn)` → `DreamReport`
- `render_report(report)` → deterministic markdown string
- `audit_retrieval_noise(results)` → `RetrievalAuditReport` (useful vs noisy;
  pure, never writes memory)

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

Provider installation, real cron scheduling, and verification against a live
Hermes install remain future work. See [`../ROADMAP.md`](../ROADMAP.md).
