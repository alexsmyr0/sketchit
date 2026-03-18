# SketchIt Decision Log

Use this file for decisions that should not stay in someone's head or get buried in chat.

## How to use this log

- Add a new entry when the team makes a decision that affects multiple issues.
- Keep entries short.
- Link to the issue or PR where the decision was made if useful.
- If a decision changes later, make sure to update this file accordingly.

## D-001: MVP is guest-only

- Decision: The MVP will not include sign-in or registration. All players are guests.
- Why: This keeps the first version simpler and avoids adding authentication complexity before the core gameplay works.
- Implications:
  - Do not model gameplay around Django user accounts.
  - Player identity should be temporary and scoped to a room, session, or current game.
  - Do not add login, signup, password, or account management flows.
  - Persistent player statistics are out of scope unless this decision changes later.

## D-002: Server controls core game rules

- Decision: The backend is authoritative for round flow, scoring, timers, turn order, and guess validation.
- Why: This reduces desynchronization and makes cheating or accidental client-side rule drift less likely.
- Implications:
  - Frontend code should send intents and render state updates.
  - Business rules should live in backend services, consumers, or models rather than browser-only logic.

## D-003: Local development can use fallbacks

- Decision: Local work may use SQLite and the in-memory channel layer until MySQL and Redis are fully available for the team.
- Why: This keeps setup from blocking implementation.
- Implications:
  - Team members can develop HTTP-side features without waiting on full infrastructure.
  - Production-oriented behavior still needs validation against MySQL and Redis later.

## D-004: Reference code is not product scope

- Decision: `Example_starting_code/` may inspire implementation details, but it does not override current MVP scope.
- Why: The reference material includes features, such as user accounts, that the current project has explicitly excluded.
- Implications:
  - When borrowing from the reference code, verify it matches current scope before implementing it.
  - AI prompts should explicitly state current project constraints so copied patterns do not drift toward the reference project.
