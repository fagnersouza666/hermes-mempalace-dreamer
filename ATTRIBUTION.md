# Attribution

This project is a **clean-room** implementation. It reuses *ideas and
structure* from public memory-dreaming work; it does **not** vendor, copy, or
redistribute upstream skill text, code, or prompts.

## Inspirations (ideas only)

- **Pluton** — the `MINE → CURATE → COMPRESS → CONTEXT` framing and the
  analytical-perspectives idea informed the dreaming pipeline shape.
- **`nexus9888/hermes-memory-skills`** — the Light / Deep / REM split and the
  "lean-check" discipline informed the report-only audit and the weekly
  lean-check cron prompt. No upstream skill text is included; license for
  vendoring upstream content has **not** been confirmed, so nothing upstream
  is bundled.
- **MemPalace Hermes plugins** — the native-provider, semantic-recall, diary
  and knowledge-graph ideas informed the MemPalace-first backend policy.

## Boundaries

- Only the *concepts* above were used; all wording, code, and prompts in this
  repository were written from scratch for this project.
- No third-party skill, prompt, or source file is vendored. The bundled
  MemPalace provider artifacts under `mempalace_dreaming/provider_bundle/`
  are first-party and profile-safe.
- The REM-style `integration-report` heuristics are intentionally simple
  (keyword / polarity / overlap). They are *not* derived from any upstream
  algorithm and do not claim semantic intelligence.
- If upstream content is ever vendored, it will only happen after its license
  is explicitly confirmed, with attribution recorded here.

## License

This repository is MIT-licensed (see [`LICENSE`](LICENSE)). The MIT terms
apply only to the first-party content of this repository.
