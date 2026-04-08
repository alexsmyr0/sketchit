# SketchIt PRD

## Purpose

This document is the clean product source of truth for the SketchIt MVP.
It describes what the team is building, who it is for, which behaviors are in scope, and which rules later ticket writing must preserve.

This is an internal planning document, not a course-report document.
Implementation details belong in the SDS.

## Product Summary

SketchIt is a browser-based multiplayer drawing and guessing game inspired by Skribbl-style gameplay.
Players create or join shared rooms, one player draws a hidden word, and the other players try to guess it in real time.

The MVP goal is one technically solid playable version of that loop:

- create and join rooms
- gather in a live lobby
- start a game
- play through synchronized drawing and guessing rounds
- score the round and game correctly
- automatically move into the next game if players remain

## Goal Of The MVP

The MVP exists to prove the full live game loop works end to end in the browser.

Success means a group of guests can:

1. create or join a room
2. see the same lobby state
3. start a game with valid participants
4. play synchronized drawing and guessing rounds
5. receive correct scoring and leaderboard updates
6. finish the game cleanly and continue if players stay in the room

## Target Users And Identity Model

The MVP is guest-only.

- There is no sign-in or registration flow.
- There are no persistent user accounts.
- Player identity is scoped to the Django session.
- A guest can only belong to one room at a time.
- Nicknames are chosen at join time and cannot be changed after joining.
- Nicknames do not need to be unique within a room.

## Core User Flow

### 1. Create Room

A guest creates a room by providing:

- room name
- room visibility (`public` or `private`)
- display name

On success:

- the room is created in `lobby`
- the creator becomes the first participant
- the creator becomes the initial host
- the room receives a unique `join_code`
- the room is assigned one word list

### 2. Join Room

A guest joins by room URL or `join_code`.

- Public rooms may appear in the room list and also remain joinable by URL.
- Private rooms are not listed and are only reachable by URL.
- The room capacity is `6` total participants, including spectators.
- Rejoining with the same Django session reuses the same participant slot.

### 3. Lobby

The lobby is the pre-game room state.

In the lobby, participants can:

- see room name, code, visibility, host, and participant list
- wait for enough players to start

Only the host can start a game.
Only the room `name` and `visibility` are host-editable in the MVP, and only while the room is in `lobby`.

### 4. Play Game

When the host starts a game:

- the minimum player count is `2`
- the server selects the active drawer
- the server selects the word from the room's chosen word list
- the drawer sees the full word
- non-drawers see only hidden-word information needed to guess
- drawing and guessing happen in real time

### 5. Finish Game

A game consists of one full cycle of drawing turns with fresh scores.

- each eligible drawer draws once per game
- the same word must not appear twice within the same game
- when all eligible drawers have completed a turn, the game ends
- a leaderboard is shown for `20` seconds

### 6. Auto-Start Next Game

After the leaderboard cooldown:

- if players remain in the room, a new game starts automatically
- the new game starts with fresh scores
- the room's current settings are copied into the new game as a snapshot

## Core Gameplay Rules

### Room Rules

- A room has a `name` and a unique random `join_code`.
- The join URL is derived from the application and is not stored in the database.
- Room visibility is `public` or `private`.
- If the host leaves, another random remaining participant becomes the new host.
- The MVP does not include kick controls or manual host transfer.
- If a room becomes empty, it stays joinable for `10` minutes and is then hard-deleted.

### Participant Rules

- All players are guests.
- A participant can be `connected` or `disconnected`.
- A participant can be `playing` or `spectating`.
- There is no ready-check system in the MVP.
- A player who joins mid-game becomes a spectator for the current turn and cannot guess during that turn.
- On the next turn, a mid-game joiner is added to the game's remaining eligible drawer pool.
- If a non-drawer disconnects and reconnects during the same game, they reclaim their place and score.

## Turn And Game Rules

- A drawing round lasts `90` seconds.
- The countdown between turns is `10` seconds.
- If the active drawer disconnects, the round waits `15` seconds before ending as `drawer_disconnected`.
- The round timer continues to run while the round is active.
- A round ends when one of the following is true:
  - the `90`-second timer expires
  - all eligible non-drawer guessers have already guessed correctly
  - the active drawer disconnect grace expires
- A game is cancelled if all players leave mid-game.
- Live timer ticking belongs to runtime state, not persisted timer records.

## Scoring Rules

Scores apply only to the current active game.
Scores reset when a new game starts.

### Correct Guesser Score

Each player's first correct guess in a round earns a time-based score.

- scoring is linear by remaining round time
- score is bounded from `100` down to `20`
- earlier correct guesses earn more points
- a player can only earn correct-guess score once per round

### Drawer Bonus

The drawer earns a smaller bonus for each other participant who guesses correctly.

- bonus is computed separately for each successful guesser
- bonus is linear by remaining round time at the moment that guess becomes correct
- bonus is bounded from `50` down to `10`

### Not In Scope For Scoring

- persistent career statistics
- stored score-event history after the game ends
- a stored winner field in the database

The winner is derived from current game scores when needed.

## Guess Feedback Rules

All chat-style submissions in the MVP are guesses.
There is no separate chat system.

Each submitted guess may produce one of these outcomes:

- `correct`
- `incorrect`
- `near_match`
- `duplicate`

Additional product rules:

- duplicate handling is same-player only
- once a player is already correct in a round, later guesses from that player are ignored for scoring
- other players may still guess and score while the round remains active

### Near Match Behavior

Near-match feedback exists to tell the player they are close without awarding a correct guess.

- For multi-word targets, a guess is `near_match` if it exactly matches one full word from the target phrase but not the full phrase.
- For single-word targets, a guess is `near_match` only under a stricter prefix or stem-style rule rather than loose fuzzy matching.

## Drawing Rules

- Drawing is synchronized live during the round.
- Drawing state is not persisted as historical data after the game ends.
- Reconnecting participants should be able to see the current drawing state for the active round.

## Out Of Scope

The MVP does not include:

- sign-in, registration, or user accounts
- persistent player profiles or statistics
- long-term match history
- separate chat outside of guesses
- persisted stroke history or canvas history after the game ends
- moderator features such as kicking players
- manual host transfer controls
- advanced room permissions
- post-MVP alternate modes such as dual-drawer gameplay

## MVP Success Criteria

The MVP is successful when all of the following are true:

- guests can create and join rooms without accounts
- a room can host a playable live game from lobby to results
- drawing and guessing stay synchronized enough for normal play
- server-side rules control round flow, scoring, timers, and turn order
- reconnect behavior preserves place and score for eligible returning participants
- rooms recover cleanly from host leaves, drawer disconnects, and empty-room cleanup

