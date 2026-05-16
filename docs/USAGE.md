# Usage

Production-ready bootstrap v1.0. Every command is dry-run / read-only by
default. Side effects happen **only** behind explicit flags (`--apply`,
`--create-cron`, `--verify-after-apply`). Nothing here installs the MemPalace
provider package, writes to Obsidian, or writes any memory.

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
hermes mempalace-dreaming setup-plan --hermes-home ~/.hermes --schedule-dreaming --time 05:30 --timezone America/Sao_Paulo
```

Prints the setup plan (directories, `hermes config set ...` commands,
optional schedule). Never applies anything. With `--schedule-dreaming` the
`schedule` block carries the requested `time`/`timezone` **and** the
UTC-converted `cron_utc` (see
[Timezone-aware scheduling](#timezone-aware-scheduling)).

### `setup` — dry-run by default, opt-in apply

```bash
hermes mempalace-dreaming setup                                    # dry-run JSON (no side effects)
hermes mempalace-dreaming setup --apply                            # create dirs + run config commands
hermes mempalace-dreaming setup --apply --schedule-dreaming --create-cron        # also create the daily cron
hermes mempalace-dreaming setup --apply --verify-after-apply       # apply, then read-only verify
```

`--apply` creates the planned directories and runs `hermes config set ...`
(argv lists, no shell). The first failing action stops the rest and is
reported in the JSON `errors` field; rollback notes are always printed.

`--create-cron` (only with `--apply`, and only if `--schedule-dreaming`
included a schedule) creates the daily dreaming cron through an injected
`schedule_fn`. The argv matches the real `hermes cron create` contract:
`--name`/`--deliver`/`--skill` options followed by the cron expression and
the conservative prompt as **positional** arguments (no `--schedule` /
`--prompt` flags). The job name is deterministic
(`mempalace-dreaming-daily`) and `--deliver` is `local` so a schedule never
broadcasts to chats. The cron expression is the **UTC** conversion of the
requested `--time`/`--timezone` (see
[Timezone-aware scheduling](#timezone-aware-scheduling)). The result is
reported under `cron` (`created`/`argv`/`error`); a cron failure — including
an unknown timezone — is captured, not raised. Without `--create-cron`,
scheduling stays report-only.

`--verify-after-apply` (only with `--apply`) runs the read-only runtime
check after a clean apply and embeds it under `verification`. It is skipped
(with a recorded reason) if apply failed early — it never inspects a
half-applied environment.

Even with every flag set, setup never installs the MemPalace provider
package, writes to Obsidian, or writes memory.

### `schedule-plan` — report-only

```bash
hermes mempalace-dreaming schedule-plan --time 05:30 --timezone America/Sao_Paulo
```

Prints only a JSON schedule plan describing what a conservative daily
dreaming cron would look like. **No cron job is created.** Schedule it
yourself with your Hermes cron tooling if you want automation. The plan
shows the requested `time`/`timezone` **and** the resulting `utc_time` /
`cron_utc` (see [Timezone-aware scheduling](#timezone-aware-scheduling)).

### Timezone-aware scheduling

The Hermes scheduler interprets cron expressions in **UTC**. To avoid lying
about "local time", scheduling is timezone-aware:

- `--time HH:MM` is a wall-clock time interpreted in `--timezone`.
- `--timezone` takes an IANA name (e.g. `America/Sao_Paulo`, `UTC`). It
  defaults to **`UTC`** — a deterministic, honest default. The time is
  **not** silently treated as the host's local time; pass `--timezone`
  explicitly for local-time scheduling.
- The requested time is converted to UTC and emitted as `cron_utc`
  (`"MM HH * * *"`). `schedule-plan` / `setup-plan` show both the requested
  `time`/`timezone` and the resulting `utc_time` / `cron_utc` / `utc_offset`,
  and `setup --apply --create-cron` creates the cron using that UTC
  expression.
- Example: `--time 05:30 --timezone America/Sao_Paulo` (UTC−3) →
  `utc_time: "08:30"`, `cron_utc: "30 08 * * *"`.
- An unknown timezone is reported as a JSON `warnings` entry (and, on apply,
  as a non-created `cron` with an error) — never a traceback.
- DST caveat: a daily cron fires at one fixed UTC instant. For zones that
  observe daylight saving time, the wall-clock run time shifts by the DST
  delta during the part of the year with the other offset. The offset is
  resolved from a fixed reference date so conversion stays deterministic;
  the plan output includes a `dst_caveat` note.

### `doctor` — read-only operational audit

```bash
hermes mempalace-dreaming doctor
hermes mempalace-dreaming doctor --hermes-home ~/.hermes
hermes mempalace-dreaming doctor --expected-time 08:30 --timezone America/Sao_Paulo
```

Runs a complete read-only audit of the installation and prints a JSON report.
It is strictly **read-only**: it runs only `hermes --version`, `hermes memory
status`, `hermes config get <key>`, and `hermes cron list` (all via an
injectable `run_fn`) and stats files. It **never** writes to config, memory,
cron, Obsidian, or any file. The report is always JSON-serializable and always
carries `ok`, `warnings`, `recommendations`, and `checks`.

#### Checks performed

1. **Plugin presence** — whether the bundled skill file, `mempalace_dreaming.engine`, and `mempalace_dreaming.setup` are present on disk.
2. **Memory / Hermes CLI** — whether `hermes --version` succeeds (CLI callable) and `hermes memory status` returns a `mempalace` provider.
3. **Config coherence** — reads each of these keys via `hermes config get` and checks their values:
   - `memory.memory_enabled` (expect truthy)
   - `memory.user_profile_enabled` (expect truthy)
   - `memory.provider` (expect `"mempalace"`)
   - `plugins.mempalace_dreaming.enabled` (expect truthy)
   - `plugins.mempalace_dreaming.skill` (expect `"plugin:mempalace-dreaming"`)

   Mismatches append specific warnings and recommendations to the report.
4. **Cron inspection** — parses `hermes cron list` output tolerantly (table, key-value, or JSON). Reports:
   - `daily_job_present`: whether `mempalace-dreaming-daily` exists;
   - `dreaming_jobs`: list of parsed jobs that look dreaming-related (matches names containing "dream", "sonho", or "mempalace-dreaming");
   - `duplicate_dreaming_jobs`: true if more than one dreaming-like job is found (including legacy names like `Sonhos diários MemPalace`).

#### Optional schedule comparison

```bash
hermes mempalace-dreaming doctor --expected-time 05:30 --timezone America/Sao_Paulo
```

When `--expected-time` is provided together with `--timezone`, the tool
converts the requested wall-clock time to a UTC cron via the same
`convert_to_utc_cron` logic used by `schedule-plan`. It then compares that
expected UTC cron against the schedule of the `mempalace-dreaming-daily` job,
field-by-field (integer comparison for minute/hour, so `8` and `08` are
treated as equal). `schedule_mismatch` is:

- `false` — daily job exists and its schedule matches the expected UTC cron;
- `true` — daily job exists but its schedule differs (warning + recommendation added);
- `null` — no `--expected-time` given, or the daily job is absent, or the timezone was invalid.

An unknown `--timezone` adds a warning and sets `schedule_mismatch` to `null`
(never a traceback; never a spurious mismatch claim).

**Omitting `--expected-time`** keeps `schedule_mismatch` null and makes no
schedule-mismatch claim. Presence and duplicate checks still run.

#### ok / warnings / recommendations

`ok` is `true` only if all checks pass: CLI callable, memory status ok, provider
is mempalace, skill/engine/setup present, config coherent, daily job present,
no duplicate jobs, and `schedule_mismatch` is not `true`.

Each failing condition appends a specific warning; recommendations give the
concrete next action (e.g. `hermes config set memory.provider mempalace`, or
"run `hermes cron list` and remove the duplicate/legacy job by id").

Doctor reports, it does **not** fix. No config writes, no cron changes, no
memory or file writes.

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
- No cron creation without explicit `setup --apply --create-cron`; the
  created cron uses the UTC conversion of `--time`/`--timezone` (default
  timezone is UTC, never silently "local time").
- No post-apply verification without explicit `--verify-after-apply`;
  skipped if apply failed early.
- No Obsidian writes.
- No memory writes during setup, verification, `status`, or `schedule-plan`.
- No built-in memory fallback for normal durable facts.
- Unknown backend fallback is report-only.
- Memory deletion/compaction must be explicit and user-approved.
- No hidden side effects at import/register time.
- `doctor` is read-only: runs only read commands (`hermes --version`, `hermes memory status`, `hermes config get`, `hermes cron list`) and stats files; never writes config, memory, cron, Obsidian, or any file.

## Production scope

This is a production-ready *bootstrap and orchestration layer*. Explicit
apply, explicit cron creation, and explicit post-apply verification are
implemented, dependency-injected, and covered by unit + integration-style
tests against an isolated fake Hermes home.

It assumes a Hermes MemPalace provider is **already available** in the
environment. Installing that provider package is external and intentionally
out of scope. See [`../ROADMAP.md`](../ROADMAP.md).
