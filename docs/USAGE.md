# Usage

Production-ready bootstrap v1.0. Every command is dry-run / read-only by
default. Side effects happen **only** behind explicit flags (`--apply`,
`--install-provider`, `--create-cron`, `--verify-after-apply`). Provider
bootstrap is now supported explicitly: the plugin can copy a bundled
`mempalace` provider into `$HERMES_HOME/plugins/mempalace/` and install the
`mempalace` CLI using a configurable, non-`uv`-only strategy
(`--install-method auto|uv|pipx|pip-user`). It still never writes to Obsidian
or any memory.

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

When `--hermes-home` is omitted, runtime commands honor the **active** Hermes
home: they prefer the host helper, then `HERMES_HOME`, and only finally fall
back to `~/.hermes`. This matters for fresh/isolated-profile validation.

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
hermes mempalace-dreaming setup --install-provider                 # dry-run plan including provider bootstrap
hermes mempalace-dreaming setup --apply --install-provider         # copy provider bundle + install mempalace CLI (auto strategy)
hermes mempalace-dreaming setup --apply --install-provider --install-method pipx     # pin the install tool (uv|pipx|pip-user)
hermes mempalace-dreaming setup --apply                            # create dirs + run config commands
hermes mempalace-dreaming setup --apply --schedule-dreaming --create-cron        # also create the daily cron
hermes mempalace-dreaming setup --apply --schedule-lean-check --create-lean-check-cron   # also create the weekly lean-check cron
hermes mempalace-dreaming setup --apply --verify-after-apply       # apply, then read-only verify
```

`--apply` creates the planned directories and runs `hermes config set ...`
(argv lists, no shell). The first failing action stops the rest and is
reported in the JSON `errors` field; rollback notes are always printed.

`--install-provider` adds the real MemPalace provider bootstrap plan to the
JSON. With `--apply --install-provider`, the plugin copies the bundled
provider files into `$HERMES_HOME/plugins/mempalace/` and installs the
`mempalace` CLI using `--install-method` (default `auto`):

| Method     | argv (never a shell string)                                            |
|------------|------------------------------------------------------------------------|
| `uv`       | `uv tool install --upgrade mempalace`                                  |
| `pipx`     | `pipx install --force mempalace`                                       |
| `pip-user` | `<python> -m pip install --user --upgrade mempalace` (this interpreter)|
| `auto`     | tries `uv` → `pipx` → `pip-user`, in that fixed order; first that succeeds wins |

The plan/result JSON exposes `install_method` and the ordered
`install_candidates`; after apply, `provider.attempts` lists every method
tried with its outcome and `provider.cli_install.method` names the one that
won. An unknown method is reported as `install_method_error` instead of
crashing. File copying and CLI installation are dependency-injected and
reported under `provider` in the result JSON. If provider bootstrap fails
(all methods exhausted), cron creation and post-apply verification are
skipped deliberately. Rollback notes name the matching uninstall command for
the method used.

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

`--schedule-lean-check` adds a separate **weekly** lean-check schedule block
(`lean_check_schedule`) to the JSON: a report-only plan for a conservative,
**live-provider** memory audit. `--lean-check-time` (default `06:30`),
`--lean-check-weekday` (cron day-of-week, `0`=Sunday..`6`=Saturday, default
`0`) and the shared `--timezone` are converted to a weekly UTC cron
(`"MM HH * * D"`); the day-of-week is shifted if the UTC conversion crosses
midnight, so the job still fires on the intended local weekday.
`--create-lean-check-cron` (only with `--apply`, and only if
`--schedule-lean-check` produced a schedule) creates that weekly cron through
an injected `schedule_fn`, with the **distinct** deterministic job name
`mempalace-dreaming-weekly-lean-check` and `--deliver local`. Its prompt
explicitly queries the live MemPalace backend **read-only** and is strictly
report-only: it never deletes, compacts, rewrites, or persists memory, and
proposes any cleanup for explicit human approval. It is gated exactly like
the daily cron (skipped if apply/provider failed early; failures — including
an unknown timezone — captured under `lean_check_cron`, never raised).
Without `--create-lean-check-cron` the weekly schedule stays report-only.

`--verify-after-apply` (only with `--apply`) runs the read-only runtime
check after a clean apply and embeds it under `verification`. It is skipped
(with a recorded reason) if apply failed early — it never inspects a
half-applied environment.

Even with every flag set, setup still never writes to Obsidian or writes
memory.

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
status`, `hermes config path`, and `hermes cron list` (all via an injectable
`run_fn`), then reads and parses the YAML config file that `hermes config path`
points at, and stats files. It **never** writes to config, memory, cron,
Obsidian, or any file. The report is always JSON-serializable and always
carries `ok`, `warnings`, `recommendations`, and `checks`.

#### Checks performed

1. **Plugin presence** — whether the bundled skill file, `mempalace_dreaming.engine`, and `mempalace_dreaming.setup` are present on disk.
2. **Memory / Hermes CLI** — whether `hermes --version` succeeds (CLI callable) and `hermes memory status` returns a `mempalace` provider.
3. **Config coherence** — resolves the config file with `hermes config path`, reads and parses that YAML file (read-only, with `_`/`-` key-name tolerance), and checks these keys:
   - `memory.memory_enabled` (expect truthy)
   - `memory.user_profile_enabled` (expect truthy)
   - `memory.provider` (expect `"mempalace"`)
   - `plugins.mempalace_dreaming.enabled` (expect truthy)
   - `plugins.mempalace_dreaming.skill` (expect `"mempalace-dreaming:mempalace-dreaming"`)

   Mismatches append specific warnings and recommendations to the report. If
   the config cannot be resolved, read, or parsed (empty `config path`, missing
   pyyaml, unreadable file, invalid YAML, non-mapping content), a
   `config_error` reason is reported instead of per-key results — no value is
   guessed and no fix is invented.
4. **Cron inspection** — parses `hermes cron list` output tolerantly (table, key-value, or JSON). Reports:
   - `daily_job_present`: whether `mempalace-dreaming-daily` exists;
   - `dreaming_jobs`: list of parsed jobs that look dreaming-related (matches names containing "dream", "sonho", or "mempalace-dreaming");
   - `duplicate_dreaming_jobs`: true if more than one **daily/legacy** dreaming-like job is found (including legacy names like `Sonhos diários MemPalace`). The distinct weekly lean-check job `mempalace-dreaming-weekly-lean-check` is excluded from this duplicate detector on purpose.

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

### `repair-plan` — report-only

```bash
hermes mempalace-dreaming repair-plan
hermes mempalace-dreaming repair-plan --hermes-home ~/.hermes
hermes mempalace-dreaming repair-plan --expected-time 08:30 --timezone America/Sao_Paulo
```

Turns the problems `doctor` detects into an explicit, machine-readable repair
plan and prints it as JSON. It reuses `build_doctor_report` internally for
detection (same read-only commands, same flags), then maps each failed check
into a `repairs` list. It is **strictly report-only**: it never writes config,
cron, memory, Obsidian, or files, and **never executes** any command — every
`command_preview` is a string the operator may choose to run, not an action
taken here.

The payload carries `plugin`, `version`, `hermes_home`, `ok`, `summary`,
`warnings`, and `repairs`. Each repair item has `id`, `priority`
(`high` → `medium` → `low`), `kind` (`config` / `cron` / `plugin` /
`memory` / `environment`), `reason`, `suggested_action`, and an optional
`command_preview` (string or `null`). `repairs` is ordered by priority.

Honesty constraints, by design:

- When `doctor` is fully green, `ok` is `true` and `repairs` is `[]`.
- It does **not** auto-fix. A `command_preview` like
  `hermes config set memory.provider mempalace` is a suggestion, never run.
- It **never invents a cron job id**: the duplicate-cron repair tells you to
  run `hermes cron list` and remove the offending job by its real id yourself.
- If the config could not be read, it reports a config-readability repair and
  suggests **no** `hermes config set` until the config is readable.
- If the `hermes` CLI is not callable, it gives a manual instruction with
  `command_preview: null` — no magic fix.
- It never raises a traceback; any failure degrades into the JSON report via
  the underlying doctor report.

`repair-plan` is the planning companion to `doctor`: `doctor` says what is
wrong, `repair-plan` says — without doing it — what you could run to fix it.

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

### `integration-report` — report-only

```bash
hermes mempalace-dreaming integration-report --input-file memories.txt
hermes mempalace-dreaming integration-report --json-input '["User always uses tabs", "User never uses tabs"]'
hermes mempalace-dreaming integration-report                 # no input -> valid JSON + warning
```

A conservative, deterministic, **REM-style** integration analysis over
already-mined memory material. Same input plumbing as `lean-check`
(`--input-file` / `--json-input`). It reports three signals and **nothing
else**:

- `contradictions` — two memories about the same topic (≥2 shared
  significant terms) with opposing polarity (`always` vs `never`, …) or a
  hand-picked antonym pair (tabs vs spaces, sync vs async, …);
- `supersede_candidates` — two memories sharing a deterministic topic key
  where exactly one carries a recency/override marker (`now`, `instead`,
  `no longer`, …); the marked one is the likely newer statement;
- `clusters` — memories grouped by an identical topic key (size ≥ 2),
  surfacing near-duplicate / consolidation candidates.

It does **not** claim semantic intelligence: the heuristics are keyword /
polarity / overlap based and the output is byte-stable for a given input.
Secret-like and temporary/progress entries are **excluded** from the
analysis (counted under `skipped`; secret text is never echoed). It is
**strictly report-only**: it never reads or writes memory, never deletes or
supersedes anything, and touches no cron/config/Obsidian/files. Resolution
of every finding is manual and report-first.

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
- `build_integration_report(memories)` → report-only JSON dict
  (contradictions / supersede candidates / clusters; deterministic, pure,
  no writes, no deletes)

## Safety model

- No config mutation without the explicit `setup --apply` flag.
- No provider bootstrap without explicit `setup --apply --install-provider`.
- No cron creation without explicit `setup --apply --create-cron`; the
  created cron uses the UTC conversion of `--time`/`--timezone` (default
  timezone is UTC, never silently "local time").
- No weekly lean-check cron without explicit `setup --apply
  --create-lean-check-cron`; its prompt is read-only against the live
  provider and never deletes/compacts/persists memory.
- `integration-report` is strictly report-only: it never reads or writes
  memory, never deletes or supersedes anything, and touches no
  cron/config/Obsidian/files.
- No post-apply verification without explicit `--verify-after-apply`;
  skipped if apply failed early.
- No Obsidian writes.
- No memory writes during setup, verification, `status`, or `schedule-plan`.
- No built-in memory fallback for normal durable facts.
- Unknown backend fallback is report-only.
- Memory deletion/compaction must be explicit and user-approved.
- No hidden side effects at import/register time.
- `doctor` and `repair-plan` are read-only: they run only read commands (`hermes --version`, `hermes memory status`, `hermes config path`, `hermes cron list`), parse the resolved YAML config, and stat files; they never write config, memory, cron, Obsidian, or any file, and never execute any `hermes config set` / `hermes cron` command.

## Production scope

This is a production-ready *bootstrap and orchestration layer*. Explicit
apply, explicit cron creation, and explicit post-apply verification are
implemented, dependency-injected, and covered by unit + integration-style
tests against an isolated fake Hermes home.

It now includes explicit provider bootstrap for Hermes profiles with a
non-`uv`-only install strategy (`auto|uv|pipx|pip-user`), and a deterministic
fresh/fake `$HERMES_HOME` smoke in the test suite. What stays necessarily
external: running against a *live* MemPalace backend and a real Hermes
gateway — the package manager that ultimately fetches `mempalace` must still
exist on the target host. Runtime validation also hardened active-home
resolution (`HERMES_HOME` / host helper) and made `doctor` treat the weekly
lean-check cron as distinct from the daily dreaming cron. See
[`../ROADMAP.md`](../ROADMAP.md).
