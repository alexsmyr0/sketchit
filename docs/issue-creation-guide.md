# SketchIt Issue Creation Guide

This guide is for planning work, creating implementation issues, and keeping shared context out of people's heads.

## Main rule

Do not rely on memory for shared implementation context.

If a rule matters in more than one issue, put it in:

- `docs/project-context.md`

## What should live where

Use `docs/project-context.md` for stable constraints such as:

- guest-only MVP
- server-authoritative gameplay
- which app owns which responsibility

It should also hold key project decisions such as:

- "players are guests, not Django users"
- "room host can start the game only when at least 2 players are present"
- "late joiners can watch immediately but only participate next round"

Use GitHub issues for:

- one implementation task
- issue-specific constraints
- acceptance criteria
- verification steps

## How to write issues without overloading yourself

Do not try to write every technical detail from scratch each time.

Instead, split the information into layers:

- Layer 1: project-wide context and decisions in `docs/project-context.md`
- Layer 2: issue-specific instructions in the GitHub issue

That means the issue only needs to explain what is unique about that task.

## What to include in every implementation issue

- one-sentence goal
- why the task exists
- exact scope
- out-of-scope items
- non-obvious constraints
- likely files or apps involved
- acceptance criteria

## How to scope work

Make issues narrow to keep implementation straightforward.

Good issue shape:

- one model or one endpoint
- one websocket event flow
- one UI component hooked to an existing backend response
- one migration plus the code that uses it

## Recommended weekly rhythm

- 15-minute planning sync
- pick only a few ready issues for the week
- mid-week async check-in in GitHub comments or chat
- short review pass before merge

## When to stop and write a decision into project-context

Update `project-context.md` when:

- the answer will likely matter again and needs to be known by an LLM
- someone asked a question that revealed important hidden context for implementation
- an issue cannot be specified cleanly without referencing the same context again
- AI keeps making the same wrong assumption
