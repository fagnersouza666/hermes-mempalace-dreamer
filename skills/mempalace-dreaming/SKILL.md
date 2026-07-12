---
name: mempalace-dreaming
description: Use when consolidating Hermes memory, running agent dreaming, adapting Hermes memory-skills ideas, auditing memory quality, or scheduling MemPalace-first memory maintenance. Uses MemPalace as the primary semantic store and keeps built-in MEMORY.md/USER.md compact.
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [memory, mempalace, dreaming, consolidation, hygiene, lean-check]
    related_skills: []
---

# MemPalace Dreaming

## Overview

This skill is a clean-room MemPalace-first implementation inspired by public Hermes/OpenClaw memory-dreaming patterns:

- Pluton: `MINE → CURATE → COMPRESS → CONTEXT` and analytical perspectives.
- nexus9888/hermes-memory-skills: Light/Deep/REM structure and lean-check discipline.
- MemPalace Hermes plugins: native provider, semantic recall, diary, KG, and prefetch ideas.

It does not vendor upstream text. It uses the ideas, then makes MemPalace the primary backend.

## Backend Contract

MemPalace is the source of long-term semantic memory.

Required tools when available:

- `mempalace_status` — inspect palace health and available wings/rooms.
- `mempalace_search` — search before making claims or writing new memories.
- `mempalace_remember` — store durable memories.

If the active backend is unknown, unknown backend fallback is report-only. Do not silently write ordinary durable facts into built-in memory.

Operational install/audit helpers now exist in the plugin surface as well:

- `hermes mempalace-dreaming verify-runtime`
- `hermes mempalace-dreaming doctor`
- `hermes mempalace-dreaming repair-plan`

Use them to audit whether Hermes is actually pointed at a MemPalace-backed
profile before assuming the backend is healthy.

Use built-in `memory()` only for boot-critical facts that must be present before tools run:

- stable user preferences affecting every response;
- safety constraints;
- essential paths or provider quirks.

## Dreaming Pipeline

### 1. MINE

Review recent sessions with `session_search()` and targeted queries. Extract candidate material:

- durable user preference or correction;
- stable environment or project fact;
- decision with rationale;
- recurring workflow;
- contradiction/conflict;
- possible skill or skill patch;
- operational dream/idea worth tracking.

Reject immediately:

- task progress;
- PR/issue/SHA/branch state;
- raw logs;
- facts likely stale within seven days;
- secrets, credentials, `.env`, tokens, connection strings;
- speculation not confirmed by user or tools.

### 2. SCORE

Score candidates before writing:

- Durability: useful for weeks/months?
- Future utility: prevents the user repeating context?
- Confidence: backed by user/tool output?
- Sensitivity: safe to store?
- Granularity: concise enough to retrieve cleanly?

Only high-confidence, durable facts proceed.

### 3. DEDUPE

Before every write, call `mempalace_search` with the core terms. If semantic duplicate exists, do not write again.

### 4. REMEMBER

Write concise declarative facts with `mempalace_remember`.

Good:

```text
Hermes MemPalace dreaming treats MemPalace as primary semantic memory and uses built-in memory only for boot-critical facts.
```

Bad:

```text
Today we created cron job abc123 and it worked.
```

### 5. INTEGRATE

Use REM-like reasoning to identify:

- contradictions to resolve;
- facts to supersede;
- recurring workflows that deserve skills;
- provider/setup gaps;
- duplicated or stale memory clusters.

In cron mode, report these. Do not mutate config, cron, files, skills, Obsidian, or built-in memory automatically.

### 6. REPORT

Return a short report:

- memories saved;
- duplicates ignored;
- conflicts or stale candidates;
- candidate skills;
- operational dreams.

If there is nothing durable, say so briefly.

## Lean Check for MemPalace

MemPalace does not have the built-in memory character limit. Its failure mode is noisy retrieval.

Audit for:

- duplicate semantic clusters;
- stale temporary task state;
- overly broad memories;
- sensitive content;
- contradictions;
- memories that should be skills instead.

Deletion/compaction is report-first and requires explicit user approval.

## Cron Policy

Daily dreaming must be conservative:

- search sessions;
- promote only high-confidence durable facts;
- dedupe first;
- do not write files/config/skills/cron/Obsidian;
- do not delete anything;
- keep report short.

Recommended split:

- daily light dreaming;
- weekly lean-check report;
- manual cleanup only after approval.

## Verification Checklist

- [ ] Confirm MemPalace is active or report mismatch.
- [ ] Use `mempalace_search` before `mempalace_remember`.
- [ ] Reject temporary facts and secrets.
- [ ] Built-in memory is not used for normal durable facts.
- [ ] Unknown backend fallback is report-only.
- [ ] Cron mode does not mutate unrelated systems.
