# SketchIt Team Workflow

This guide explains how each team member should implement an assigned issue.

Issue planning, issue writing, and team coordination guidance lives in `docs/issue-creation-guide.md`.

## Main rule

Do not rely on memory for shared implementation context.

If a rule matters in more than one issue, put it in:

- `docs/project-context.md` for stable project-wide rules
- `docs/decision-log.md` for decisions the team has made

## Shared context to read before coding

Use `docs/project-context.md` for stable constraints such as:

- guest-only MVP
- server-authoritative gameplay
- which app owns which responsibility

Use `docs/decision-log.md` for choices such as:

- "players are guests, not Django users"
- "room host can start the game only when at least 2 players are present"
- "late joiners can watch immediately but only participate next round"

## Workflow for implementing an issue

1. Read `docs/project-context.md`, `docs/decision-log.md`, and the issue.
2. Leave a short comment before coding:
   - what you think the task means
   - what files you expect to touch
   - any missing decision that blocks you from full implementation
3. Only then start implementation.
4. Open a small PR and use the PR template.
5. If a new project-level rule is discovered, add it to the decision log before merge.

This short comment step is important. It catches misunderstandings before code is written.

## AI usage rule for the team

- AI can help draft code, tests, and refactors.
- AI should not make product-scope decisions on its own.
- You MUST give AI proper context before letting it assist you in implementation (This means giving it project-context.md & decision-log.md files along with the issue at hand and any conversation had about it)

Minimum context to include in any AI prompt:

- Project-Context.md & decision-log.md file
- Issue at hand along with any conversation in that issue

If you do not include those points, the AI will often generate/guess its own rules and implementation.

## When to stop and write a decision down to decision-log

Write to the decision log when:

- the answer will likely matter again and needs to be known by an LLM
- someone asked a question that revealed important hidden context for implementation
- an issue cannot be specified cleanly without referencing the same context again
- AI keeps making the same wrong assumption
