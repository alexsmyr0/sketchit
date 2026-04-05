# SketchIt Team Workflow

This guide explains how each team member should implement an assigned issue.

Issue planning, issue writing, and team coordination guidance lives in `docs/issue-creation-guide.md`.

## Main rule

Do not rely on memory for shared implementation context.

If a rule matters in more than one issue, put it in:

- `docs/mvp-spec.md`
- `docs/progress-context.md`

## Shared context to read before coding

Use `docs/mvp-spec.md` for stable product constraints such as:

- guest-only MVP
- server-authoritative gameplay
- which app owns which responsibility

It also contains important project decisions such as:

- "players are guests, not Django users"
- "room host can start the game only when at least 2 players are present"
- "late joiners can watch immediately but only participate next round"

Use `docs/progress-context.md` for the current codebase snapshot, such as:

- what is implemented already
- what is still stubbed
- which technical layers exist but are not yet used

## Workflow for implementing an issue

1. Read `docs/mvp-spec.md`, `docs/progress-context.md`, and the issue.
2. Leave a short comment before coding:
   - what you think the task means
   - what files you expect to touch
   - any missing decision that blocks you from full implementation
3. Only then start implementation.
4. Open a small PR and use the PR template.
5. If a new product-level rule is discovered, add it to `docs/mvp-spec.md` before merge.
6. If implementation state changed in a way that affects planning, update `docs/progress-context.md` before merge.

This short comment step is important. It catches misunderstandings before code is written.

## AI usage rule for the team

- AI can help draft code, tests, and refactors.
- AI should not make product-scope decisions on its own.
- You MUST give AI proper context before letting it assist you in implementation. This means giving it `mvp-spec.md`, `progress-context.md`, the issue at hand, and any conversation about it.

Minimum context to include in any AI prompt:

- `mvp-spec.md`
- `progress-context.md`
- Issue at hand along with any conversation in that issue

If you do not include those points, the AI will often generate/guess its own rules and implementation.

## When to update shared docs

Update `docs/mvp-spec.md` when:

- the answer will likely matter again and needs to be known by an LLM
- someone asked a question that revealed important hidden context for implementation
- an issue cannot be specified cleanly without referencing the same context again
- AI keeps making the same wrong assumption

Update `docs/progress-context.md` when:

- the implementation state changes in a way that affects planning
- a previously missing system now exists
- the current snapshot would otherwise become misleading
