# SketchIt Project Context

This file is the stable source of truth for implementation rules that apply across many issues.

## Product scope for the MVP

- SketchIt is a browser-based multiplayer drawing and guessing game.
- The MVP focuses on rooms, lobby flow, rounds, guesses, drawing sync, timers, and scoring.
- There is no sign-in or registration in the MVP.
- Every player is a guest.
- Players should be modeled as temporary participants in a room or game session, not as persistent user accounts.
- Do not add authentication flows, user profiles, or account-linked statistics unless a later decision explicitly changes the scope.

## Important clarification about reference code

- `Example_starting_code/` is reference material, not the current product specification.
- Some reference material mentions optional user accounts. That does not apply to the current MVP unless the team explicitly changes the plan.
- If code or AI suggestions copied from reference material introduce accounts, login, or persistent user identity, treat that as out of scope.

## Technical boundaries

- Backend framework: Django.
- Real-time layer: Django Channels.
- Persistent database target: MySQL.
- Local fallback database during setup: SQLite.
- Real-time broker target: Redis.
- Local fallback channel layer during setup: in-memory channel layer.

## Architecture rules

- The server is authoritative for game state.
- Turn order, round state, timers, scoring, and guess validation should be decided server-side.
- Clients can send actions, but clients should not be trusted as the source of truth for game rules.
- Prefer keeping persistent data separate from temporary in-memory or real-time state.

## App boundaries

- `rooms/`: room creation, joining, lobby presence, room membership.
- `games/`: match flow, rounds, scoring, turn order, game state.
- `words/`: word lists, word selection, word-related data.
- `core/`: shared utilities and cross-app building blocks.

## Team implementation rules

- Every implementation issue must link back to this file and the decision log.
- If a task depends on a non-obvious rule, the rule must be written down before or during the issue, not left in chat memory.
- If an issue is missing behavior that blocks implementation, the assignee should ask in the issue comments before coding too far ahead.
- Prefer small issues that change one clear slice of behavior.

## Definition of ready for an implementation issue

Before assigning an issue, make sure it includes:

- the goal in one sentence
- the exact scope
- non-obvious constraints
- files or app areas likely involved
- acceptance criteria
- a short verification plan

If those are missing, the issue is not ready to assign.
