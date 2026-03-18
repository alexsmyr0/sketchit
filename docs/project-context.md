# SketchIt Project Context

This file is the single source of truth for what SketchIt is, what the MVP includes, and the decisions and constraints the team should follow while implementing it.

## General idea of the project

- SketchIt is a browser-based multiplayer drawing and guessing game inspired by Skribbl-style gameplay.
- Players join shared rooms, one player draws, and the others try to guess the word in real time.
- The MVP is focused on delivering one technically solid playable version of that core loop before expanding into extra features.

## Current decisions and constraints

### Product scope for the MVP

- The MVP focuses on rooms, lobby flow, rounds, guesses, drawing sync, timers, and scoring.
- There is no sign-in or registration in the MVP.
- Every player is a guest.
- Players should be modeled as temporary participants in a room or game session, not as persistent user accounts.
- Do not add authentication flows, user profiles, or account-linked statistics unless the team explicitly changes scope later.

### Reference code boundary

- `Example_starting_code/` is reference material, not the current product specification.
- Some reference material mentions optional user accounts. That does not apply to the current MVP unless the team explicitly changes the plan.
- If code or AI suggestions copied from reference material introduce accounts, login, or persistent user identity, treat that as out of scope.

### Technical boundaries

- Backend framework: Django.
- Real-time layer: Django Channels.
- Persistent database target: MySQL.
- Local fallback database during setup: SQLite.
- Real-time broker target: Redis.
- Local fallback channel layer during setup: in-memory channel layer.

### Architecture rules

- The server is authoritative for game state.
- Turn order, round state, timers, scoring, and guess validation should be decided server-side.
- Clients can send actions, but clients should not be trusted as the source of truth for game rules.
- Prefer keeping persistent data separate from temporary in-memory or real-time state.

### App boundaries

- `rooms/`: room creation, joining, lobby presence, room membership.
- `games/`: match flow, rounds, scoring, turn order, game state.
- `words/`: word lists, word selection, word-related data.
- `core/`: shared utilities and cross-app building blocks.

### Team implementation rules

- Every implementation issue should link back to this file.
- If a task depends on a non-obvious rule, the rule must be written down here instead of left in chat memory.
- If an issue is missing behavior that blocks implementation, the assignee should ask in the issue comments before coding too far ahead.
- Prefer small issues that change one clear slice of behavior.

### Definition of ready for an implementation issue

Before assigning an issue, make sure it includes:

- the goal in one sentence
- the exact scope
- non-obvious constraints
- files or app areas likely involved
- acceptance criteria
- a short verification plan

If those are missing, the issue is not ready to assign.

## Key decisions

### D-001: MVP is guest-only

- Decision: The MVP will not include sign-in or registration. All players are guests.
- Why: This keeps the first version simpler and avoids adding authentication complexity before the core gameplay works.
- Implications:
  - Do not model gameplay around Django user accounts.
  - Player identity should be temporary and scoped to a room, session, or current game.
  - Do not add login, signup, password, or account management flows.
  - Persistent player statistics are out of scope unless this decision changes later.

### D-002: Server controls core game rules

- Decision: The backend is authoritative for round flow, scoring, timers, turn order, and guess validation.
- Why: This reduces desynchronization and makes cheating or accidental client-side rule drift less likely.
- Implications:
  - Frontend code should send intents and render state updates.
  - Business rules should live in backend services, consumers, or models rather than browser-only logic.

### D-003: Local development can use fallbacks

- Decision: Local work may use SQLite and the in-memory channel layer until MySQL and Redis are fully available for the team.
- Why: This keeps setup from blocking implementation.
- Implications:
  - Team members can develop HTTP-side features without waiting on full infrastructure.
  - Production-oriented behavior still needs validation against MySQL and Redis later.

### D-004: Reference code is not product scope

- Decision: `Example_starting_code/` may inspire implementation details, but it does not override current MVP scope.
- Why: The reference material includes features, such as user accounts, that the current project has explicitly excluded.
- Implications:
  - When borrowing from the reference code, verify it matches current scope before implementing it.
  - AI prompts should explicitly state current project constraints so copied patterns do not drift toward the reference project.
