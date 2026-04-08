# SketchIt SDS

## Purpose

This document describes the target technical design for the SketchIt MVP.
It is grounded in the current repository and the product rules locked in the PRD.

The SDS exists to remove ambiguity before ticket writing.
Later implementation tickets should not need to invent architecture, ownership, or core runtime rules.

## System Overview

SketchIt is a Django application with a server-rendered frontend and a live multiplayer runtime.

Target stack:

- backend framework: Django
- realtime layer: Django Channels
- persistent database: MySQL
- runtime state store and broker: Redis
- frontend delivery: Django templates with vanilla JavaScript

High-level behavior:

- HTTP is used for room creation, joining, initial page delivery, and non-streaming room/game actions
- one WebSocket per room carries all live room, game, drawing, and guess events
- the server is authoritative for room state, round flow, timers, drawer rotation, scoring, and guess evaluation

## Current Implementation Baseline

The current repo is ahead of `docs/progress-context.md` in some areas.
This SDS treats the codebase, not that older summary file, as the actual baseline.

### Already Implemented

- room creation HTTP flow exists
- room join HTTP flow exists
- room lobby-state HTTP flow exists
- host-only start-game HTTP flow exists
- persistent models exist for rooms, participants, games, rounds, guesses, words, and word packs
- game start logic exists in the service layer
- basic guess evaluation exists in the service layer
- Redis helper code already exists for:
  - room presence
  - latest canvas snapshot

### Partially Implemented

- round and guess persistence exist, but current guess behavior is still a simplified placeholder
- current guess logic awards `1` point to the correct guesser and `1` point to the drawer
- current guess logic ends the round on the first correct guess
- current room/game backend does not yet match the target time-based scoring and multi-guesser round flow in this SDS

### Missing Or Largely Missing

- WebSocket consumers
- WebSocket routing beyond an empty project-level placeholder
- template-rendered product screens for lobby and gameplay
- live drawing synchronization
- full round progression after the first round
- timed round lifecycle management
- drawer disconnect handling
- reconnect reclaim flow
- live host reassignment flow
- end-of-game leaderboard and automatic next-game cycle

## Architecture Decisions

### Frontend Delivery

The MVP uses Django templates plus vanilla JavaScript.

- no separate frontend SPA
- no frontend framework dependency
- server renders the initial HTML page
- browser JavaScript hydrates live behavior after page load

This keeps the frontend aligned with the current Django project shape and avoids introducing a second application architecture.

### Realtime Topology

The MVP uses one WebSocket connection per room.

That single room socket carries:

- room presence and lobby state updates
- game and round state updates
- timer updates
- drawing events
- guess submission and guess-result events
- leaderboard and game-end events

This is the simplest coherent shape for the MVP and is sufficient unless proven otherwise by performance issues.

### Authority Model

The server is authoritative for:

- room membership
- host ownership
- round timers
- eligible drawer tracking
- word selection
- guess evaluation
- scoring
- round completion
- game completion and next-game start

Clients send intents and render server-owned state.

## App And Domain Ownership

### `rooms`

Owns:

- room creation and joining
- room visibility
- host assignment and reassignment
- room membership and participant session ownership
- lobby presence and participant connection state
- room empty-state cleanup
- room-scoped Redis helpers and room WebSocket entrypoint

### `games`

Owns:

- game lifecycle
- round lifecycle
- turn sequencing
- eligible drawer pool tracking
- score calculation
- round completion rules
- guess evaluation
- leaderboard generation

### `words`

Owns:

- word lists
- word membership in lists
- room word-list selection input
- snapshotting selected words into a started game

### `core`

Owns only shared utilities and abstractions that are genuinely cross-app.
It must not become a dumping ground for domain logic.

## Persistent Data Design

Persistent state belongs in MySQL and should remain stable across process restarts.

### Room

Persistent room fields:

- `name`
- `join_code`
- `visibility`
- `status`
- `max_players`
- `word_pack`
- `host`
- `empty_since`
- timestamps

Room status is the durable room lifecycle view:

- `lobby`
- `in_progress`
- `empty_grace`

### Participant

Persistent participant fields:

- `room`
- `session_key`
- `display_name`
- `connection_status`
- `participation_status`
- `current_score`
- `last_seen_at`
- `session_expires_at`
- timestamps

The participant row is the durable identity anchor for a guest inside a room.

### Game

Persistent game fields:

- `room`
- `status`
- `started_at`
- `ended_at`
- timestamps

Game status is:

- `in_progress`
- `finished`
- `cancelled`

### Game Word Snapshot

Each started game stores its own snapshot of candidate words.
That snapshot decouples the active game from later changes to the room's selected word pack.

### Round

Persistent round fields:

- `game`
- `drawer_participant`
- `drawer_nickname`
- `selected_game_word`
- `sequence_number`
- `status`
- `started_at`
- `ended_at`
- timestamps

Round terminal outcomes are:

- `completed`
- `drawer_disconnected`
- `cancelled`

### What Must Not Be Persisted

Do not persist these as long-term records:

- live timer ticks
- live drawing stroke history after the game ends
- guess history after the game ends
- per-event score history
- current canvas pixels as durable database data

## Runtime State Design In Redis

Redis stores live room and round state that is transient, fast-changing, or only needed during active play.

### Existing Logical Runtime Keys

These already exist in the repo and remain valid:

- `room:{join_code}:presence`
  - Redis set
  - connected session keys for the room
- `room:{join_code}:canvas`
  - Redis string
  - latest canvas snapshot bytes for reconnect sync

### Required Additional Runtime State

The target design also requires logical runtime storage for:

- active turn timing
- eligible drawers remaining in the current game
- per-round live guess state
- pending room cleanup timing

Recommended logical layout:

- `room:{join_code}:turn`
  - current round runtime snapshot
  - contains `game_id`, `round_id`, `started_at`, `deadline_at`, and any active drawer disconnect deadline
- `room:{join_code}:cycle`
  - remaining eligible drawer participant IDs for the current game
- `room:{join_code}:round:{round_id}:guess-state`
  - per-player live guess state for the active round
  - tracks which players are already correct and any same-player duplicate history
- `room:{join_code}:cleanup`
  - room empty-grace deletion deadline

`Room.empty_since` remains the durable backup for room cleanup state.
Redis cleanup timing is an acceleration aid, not the only durable source.

### Redis Ownership Rules

- Redis state is room-scoped
- Redis state is cleared when a game ends or a room is deleted
- Redis state must never become the only source of durable user-visible history
- Redis state may be rebuilt from persistent data when the information is durable enough to recover

## Interface Surfaces

### HTTP Surfaces

Existing HTTP endpoints that remain part of the system surface:

- `POST /rooms/create/`
- `POST /rooms/<join_code>/join/`
- `GET /rooms/<join_code>/`
- `POST /rooms/<join_code>/start-game/`

Target responsibilities:

- create/join remains HTTP
- host start-game may remain HTTP-backed for the MVP
- initial room page delivery uses Django templates
- room socket handles live updates after page load

The SDS does not require the team to change existing room JSON endpoints immediately.
They may be retained while template pages are added around them.

### WebSocket Surface

Target room socket: one socket per room, scoped by `join_code`.

Suggested envelope:

```json
{
  "type": "event.name",
  "payload": {}
}
```

Suggested client-to-server event families:

- `drawing.stroke`
- `drawing.end_stroke`
- `drawing.clear`
- `guess.submit`

Suggested server-to-client event families:

- `room.state`
- `host.changed`
- `game.started`
- `round.started`
- `round.timer`
- `round.ended`
- `drawing.stroke`
- `drawing.snapshot`
- `guess.result`
- `scoreboard.state`
- `game.finished`
- `game.cancelled`

### Role-Specific Payload Rule

The drawer receives the full selected word.
Non-drawers do not receive the full word and only receive the masked or partial word information needed to guess fairly.

### Session And Authorization Rule

The room socket is authorized by Django session.

- the connection must belong to a session already associated with that room
- on connect, the participant is marked connected in persistent state and Redis presence
- on disconnect, the participant is marked disconnected and removed from Redis presence

## Gameplay Rules In Technical Form

### Starting A Game

To start a game:

- room must be in `lobby`
- requester must be the current host
- there must be at least `2` connected participants with `playing` status
- the room's selected word pack must contain at least one valid word

On successful start:

- current participant scores reset to `0`
- a new `Game` row is created
- the room's selected word list is copied into `GameWord`
- the server builds the initial eligible drawer pool from connected `playing` participants
- the first drawer is selected from that pool
- the first word is selected from the game snapshot
- the room status becomes `in_progress`

### Turn Selection

The server owns the drawer pool.

- each eligible drawer can be selected only once per game
- selected drawers are removed from the remaining drawer pool
- mid-game joiners do not draw in the current turn
- after the current turn ends, a mid-game joiner with `playing` status is added to the remaining drawer pool for the current game
- when the remaining drawer pool becomes empty, the game ends

### Turn Timer

Each round lasts `90` seconds.

Let:

- `ROUND_DURATION_MS = 90000`
- `remaining_ratio = clamp(remaining_ms / ROUND_DURATION_MS, 0, 1)`

Live ticking time belongs in Redis runtime state and server scheduling logic, not in durable database rows.

### Scoring Formula

The current code does not implement the target scoring model.
Current simple scoring is treated as a temporary partial implementation.

Target formula:

```text
guesser_points = round(20 + remaining_ratio * 80)
drawer_bonus = round(10 + remaining_ratio * 40)
```

Rules:

- a participant can receive `guesser_points` only once per round
- the drawer receives `drawer_bonus` for each distinct participant whose guess becomes correct
- scoring uses the remaining time at the moment each correct guess is accepted
- score totals live on the participant record for the current game only

### Round Completion Conditions

A round ends when one of these conditions is met:

1. the `90`-second round timer expires
2. all eligible non-drawer guessers are already correct
3. the drawer disconnects and the `15`-second grace deadline expires

Round completion outcomes:

- `completed`
- `drawer_disconnected`
- `cancelled`

### Eligible Guesser Set

The eligible non-drawer guesser set for a round is fixed at round start:

- includes all `playing` non-drawer participants present in the room at round start
- excludes spectators
- excludes mid-turn joiners

Already-correct players are removed from the still-pending guesser set for early-finish checks.
Disconnected guessers remain part of the original eligible set; their disconnect alone does not award them completion or remove them from the game.

### Reconnect Reclaim Behavior

Reconnect identity is the Django session.

If a non-drawer disconnects and reconnects during the same game:

- they reclaim the same participant row
- they keep their score
- they resume as connected
- they receive the current room/game state and current canvas snapshot

If the drawer disconnects:

- start a `15`-second grace timer
- if the drawer reconnects before the deadline, continue the round
- if not, end the round as `drawer_disconnected`

### Host Reassignment

If the current host leaves the room:

- select a new host randomly from remaining participants
- broadcast the new host over the room socket
- if the room is empty, no host remains

### Empty-Room Cleanup

If the room becomes empty:

- set room status to `empty_grace`
- persist `empty_since`
- store the deletion deadline logically in runtime state
- if the room remains empty for `10` minutes, hard-delete it

If someone rejoins before deletion:

- clear the empty-room cleanup state
- return the room to `lobby`
- any active game is treated as no longer resumable and must not be restored

## Guess Evaluation Rules

### Normalization

Guess comparison uses normalized text:

- trim leading and trailing whitespace
- collapse repeated internal whitespace
- compare case-insensitively

### Outcome Order

The server should evaluate guesses in this order:

1. if the player is already correct for the round, ignore the submission for scoring and round state
2. if the normalized guess is fully correct, return `correct`
3. if it is a same-player duplicate for this round, return `duplicate`
4. if it matches the near-match rule, return `near_match`
5. otherwise return `incorrect`

### Duplicate Rule

Duplicate handling is same-player only.

- if a player sends the same normalized guess they already sent earlier in the same round, later copies are `duplicate`
- guesses sent by different players do not become duplicates just because another player already used that text

### Correct Rule

- a player can score from at most one correct guess per round
- other players may still guess correctly and score while the round remains active
- the drawer cannot guess their own word for score

### Near-Match Rule

For multi-word targets:

- split the target phrase into normalized word tokens
- if the normalized guess equals exactly one full target token, but not the full normalized phrase, return `near_match`

For single-word targets:

- return `near_match` only if the normalized guess is a strict prefix of the normalized target
- the prefix must be at least `3` characters long
- exact full-word matches are `correct`, not `near_match`

This deliberately uses a strict rule rather than loose fuzzy matching.

## Testing Strategy

### Model And Service Tests

Cover:

- room model rules
- participant session uniqueness
- game start behavior
- word snapshot behavior
- scoring formulas
- round progression
- reconnect and host-reassignment service behavior

### HTTP Tests

Cover:

- create room
- join room
- room access authorization
- host-only game start
- later room-page and room-setting flows when added

### Redis Helper Tests

Cover:

- presence set behavior
- canvas snapshot behavior
- TTL handling
- room isolation

### Consumer And WebSocket Tests

Cover:

- socket authorization by session
- connect and disconnect presence transitions
- room state broadcast
- drawing event broadcast
- guess submission and guess-result events
- timer and round-end broadcasts
- reconnect snapshot sync

### Integration Scenarios

At minimum, test these end-to-end flows:

- room create -> join -> lobby sync -> host starts game
- first round starts and timer runs
- multiple non-drawers can guess correctly in the same round
- scoring follows bounded linear formulas
- drawer disconnect grace expires correctly
- mid-game joiner spectates current turn and joins later eligibility
- final leaderboard shows and next game auto-starts if players remain
- empty room enters grace and is deleted after the deadline

## Implementation Notes For Ticketing

- The existing simplified guess logic must later be replaced, not merely extended around the edges.
- Redis runtime state should remain room-scoped and small enough to clear cleanly on game end.
- Frontend tickets should assume server-rendered pages plus room-socket hydration, not a separate SPA.
- Realtime tickets should assume one room socket, not separate drawing and game sockets.
