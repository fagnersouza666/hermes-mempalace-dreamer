# Hermes MemPalace Dreamer

MemPalace-first dreaming and memory hygiene bundle for Hermes Agent.

## Goal

One installable Hermes plugin that ships:

- a MemPalace-first dreaming skill;
- safe setup planning CLI;
- future installer hooks for a MemPalace provider and optional cron routine.

## Install, once published

```bash
hermes plugins install OWNER/hermes-mempalace-dreamer --enable
hermes mempalace-dreaming setup-plan --schedule-dreaming
```

Current state: MVP scaffold. The setup command prints a plan; it does not mutate config yet.

## Design inputs

- Pluton: MINE/CURATE/COMPRESS/CONTEXT.
- nexus9888/hermes-memory-skills: Light/Deep/REM and lean-check discipline.
- Hermes MemPalace provider plugins: native provider, prefetch, diary, KG.

We do not vendor upstream skills until license/permission is explicit.
