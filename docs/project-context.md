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

## Additional MVP decisions

These decisions were clarified after the initial project context and should be treated as part of the current source of truth for implementation planning.

### Scope

- The project should define both a persistent Django-model ERD and a separate runtime-state design for Redis, Channels, or in-memory data.
- Persistent data and temporary runtime state should stay clearly separated.
- The MVP only supports live and current games.
- Historical match storage is out of scope for now.
- Guess or chat history should not be kept after a game ends.
- Drawing stroke or canvas history should not be persisted and should stay out of the persistent ERD.

### Terminology

- All players are guests.
- A player is treated as a temporary participant, not a persistent account.
- `round` means one drawing turn.
- `cycle` means one full pass where each eligible drawer draws once.
- `game` is the term used for one full cycle with fresh scores.
- A room can host multiple games over time.
- The current preference for the MVP is a single participant concept with role or state flags such as `playing` and `spectating`, rather than separate lobby and game participant concepts.

### Room

- A room has a `name` and a unique random `join_code`.
- The full join URL is built by the application and should not be stored in the database.
- The `join_code` is an access code in the URL, not a password.
- Rooms have `public` or `private` visibility.
- Public rooms appear on the room list and are also accessible by URL.
- Private rooms are not listed and are only accessible by URL.
- The room creator becomes the initial host.
- If the host leaves at any point, another random remaining player becomes the new host.
- The MVP does not include host powers such as kicking players or manually transferring host.
- Room capacity is limited to `6` total participants, including spectators.
- Each room is associated with one word list.
- Host-editable room settings in the MVP are only `name` and `visibility`.
- Room settings should only be editable while the room is in `lobby`.
- If a room becomes empty, it stays joinable for `10` minutes and is then hard-deleted.
- Public room lists should show all rooms, including rooms already in progress.

### Guest / Participant

- Guest re-identification and reconnect behavior should use the Django session.
- The MVP does not need a separate persistent guest identity model.
- Nicknames do not need to be unique within a room.
- Nicknames cannot be changed after joining.
- A guest can only be in one room at a time.
- Participant-related states needed in the MVP include `connected`, `disconnected`, `playing`, and `spectating`.
- There is no ready-check or ready-state system in the MVP.
- If a non-drawer disconnects and reconnects during the same game, they should reclaim their place and score.
- Temporary participant and game data may stay in Redis during the active game and should be cleared when that game ends.

### Game / Turn flow

- A room starts in `lobby` when first created and stays there until the host presses Play.
- The minimum number of players required to start a game is `2`.
- Game states are `lobby`, `in_progress`, `finished`, and `cancelled`.
- If all players leave mid-game, the game is cancelled.
- When a cycle ends, the leaderboard is shown for `20` seconds.
- If players remain in the room, a new game starts automatically after that cooldown.
- Each new game starts with fresh scores.
- Game-relevant room defaults should be copied into each new game as a snapshot when that game starts.
- Each turn has exactly one drawer and one chosen word.
- Words are selected automatically from the room's selected word list.
- The same word must not appear twice within the same game or cycle.
- Drawer selection is server-controlled.
- The server should track the pool of eligible drawers still left to draw in the current cycle.
- Once a player has drawn in the current cycle, they must not be selected again in that same cycle.
- A player who joins mid-game spectates the current turn and cannot guess during that turn.
- On the next turn, a mid-game joiner is added to the current cycle's remaining eligible drawer pool.
- If the active drawer disconnects, the turn waits `15` seconds before ending as `drawer_disconnected`.
- Turn outcomes should be stored on the turn record.
- Confirmed turn outcome values include `completed` and `drawer_disconnected`.
- The countdown between turns is `10` seconds.
- Live ticking timer state should stay in runtime state rather than persisted timer records.

### Guesses / Chat / Scoring

- All chat messages are guesses. There is no separate chat system in the MVP.
- All submitted guesses should be tracked during the live game.
- Guess handling needed in live gameplay includes `correct`, `incorrect`, `near_match`, and `duplicate`.
- `too_late` is not needed as a guess status.
- Guesses are runtime-only and should not be persisted after the game ends.
- Scores are only the current game totals.
- Scores reset when a new game starts.
- Persisted per-event score history is out of scope.
- The winner should be derived from scores when needed and should not be stored directly.

### Words

- Word lists are developer-managed only in the MVP.
- Multiple word lists are supported.
- Each room uses one word list.
- Words can belong to multiple lists.
- Duplicate words are allowed for simplicity.
- Word lists only need a `name` in the MVP.
- The active game should use a snapshot or copy of the selected word list during gameplay.

### Technical

- Use integer primary keys.
- Add `created_at` and `updated_at` fields.
- Use hard delete, not soft delete, for the MVP.
- Keep app responsibilities split by domain:
  - `rooms` for room and access concerns.
  - `games` for game flow and turns.
  - `words` for words and word lists.
  - `core` for shared abstractions and utilities only.
