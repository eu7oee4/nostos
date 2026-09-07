# Prompt assembly (MVP)

Design: [DESIGN_prompt_assembly.md](./DESIGN_prompt_assembly.md) §9 steps **1 + 2**.

## What this PR landed

- **Single pipeline** `app.context.assemble.build_messages` — cache order:
  1. fixed system (`app/prompts/system.md`)
  2. optional `data/user_profile.json` block
  3. optional `data/persona.md`
  4. dialog rows with frozen `[MM-DD HH:MM]` stamps (`created_at` × `TIMEZONE`)
  5. recall suffix（有记忆才注入）
  6. time-anchor suffix every turn
  7. trigger (`user` / `wake`)
- **Timezone** via settings / `TIMEZONE` env — **not** in profile.
- **LLM usage** logged at INFO (`nostos.llm`) including raw cache-hit keys when the provider returns them.
- `Trigger` + `build_messages` exported for chat_loop; wake fire can adopt later (not rewritten here).

## Out of scope (later)

Onboarding cards / `ask_user`, episode soft/hard gates, killing random-wake script path.

## Optional local files

```json
// data/user_profile.json
{ "nickname": "眠眠", "gender": "她", "companion_gender": "他" }
```

```markdown
<!-- data/persona.md -->
温柔、简洁，会记得小事。
```
