# Design: Hermes MemPalace Dreamer

## Synthesis from existing projects

### Pluton ideas to reuse

- Dream cycle as a pipeline: `MINE -> CURATE -> COMPRESS -> CONTEXT`.
- Multiple analytical lenses, not just fact extraction.
- Threshold/schedule based consolidation.
- Testable separation between mechanics and LLM judgment.

Adaptation: replace Pluton's wiki-as-primary-memory with MemPalace semantic storage and report-first structural recommendations.

### hermes-memory-skills ideas to reuse

- Light/Deep/REM modes.
- Lean-check as memory hygiene discipline.
- Skill-first operating instructions for agents.

Adaptation: remove built-in/Holographic assumptions. Unknown backend is report-only. Normal durable facts go to MemPalace.

### Hermes MemPalace provider ideas to reuse

- Native `MemoryProvider` integration.
- Session-end diary and pre-compression distillation.
- Semantic recall and KG queries.

Adaptation: this repo starts as a dreaming/skill bundle. Provider installation will be delegated to an explicit installer step or bundled later after selecting a stable provider base.

## MVP boundaries

Current MVP ships:

- plugin metadata;
- plugin-registered skill;
- dry-run setup-plan CLI;
- tests proving registration, skill policy, and setup plan contract.

Current MVP intentionally does not:

- mutate `~/.hermes/config.yaml`;
- install MemPalace itself;
- create cron jobs;
- write memories;
- vendor third-party upstream skills with unclear license.

## Safety policy

- No secrets stored.
- No Obsidian writes.
- No built-in memory fallback for normal facts.
- Cron automation must be conservative and report-first.
- Deletion/compaction requires explicit approval.
