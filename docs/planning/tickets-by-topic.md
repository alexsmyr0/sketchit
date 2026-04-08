# SketchIt Tickets By Topic

This file is the full intermediate backlog for the SketchIt MVP.
It is grouped by implementation topic, not by final developer track.

Use this file to define the full ticket set before:

- auditing current GitHub issues and current implementation coverage
- marking tickets as done, partial, or open
- splitting the tickets into Tracks A-D

The canonical product and technical context for every ticket is:

- [prd.md](/Users/alexsmyro/dev/Medcollege/2025-2026/Team Project/Sketchit/docs/planning/prd.md)
- [sds.md](/Users/alexsmyro/dev/Medcollege/2025-2026/Team Project/Sketchit/docs/planning/sds.md)

## Working Rules

1. Ticket IDs stay sequential as `T-01`, `T-02`, and so on until track assignment.
2. Every implementation ticket must include its own tests for the added behavior.
3. There is no separate testing section for normal feature work.
4. `Quality Assurance & MVP Hardening` exists only for late cross-cutting stabilization after the main MVP slices are in place.
5. Final assignment files and tracker status are created later from this backlog.

## Phase Order (Vertical Slice)

- **P0 Existing Baseline**: `T-01`, `T-02`, `T-03`, `T-04`
- **P1 Realtime Lobby**: `T-05`, `T-06`, `T-07`, `T-08`, `T-09`, `T-10`, `T-21`, `T-22`
- **P2 Playable Round Loop**: `T-11`, `T-12`, `T-13`, `T-14`, `T-15`, `T-23`, `T-24`
- **P3 Full Game Rules**: `T-16`, `T-17`, `T-18`, `T-19`, `T-20`
- **P4 Hardening & QA**: `T-25`, `T-26`

## 1. Foundation & Shared Architecture

Scope: persistent domain baseline, room/game service baseline, and shared runtime helpers that later tickets build on.

---

#### T-01: Persistent Domain Models & Word Setup Baseline
**Priority**: Critical  
**Phase**: P0 Existing Baseline  
**Depends On**: None  
**Impacts**: Durable schema ownership for rooms, participants, games, rounds, guesses, and word lists  
**Blocks**: `T-02`, `T-03`, `T-04`, `T-11`, `T-14`

**Deliverables**:
- Persistent Django models for rooms, participants, games, rounds, guesses, words, and word packs
- MySQL migrations for the MVP baseline schema
- Room-to-word-pack association and default word-pack wiring
- Seeded default word pack for local development and tests

- [ ] Define or confirm integer-primary-key Django models for `rooms`, `games`, and `words` domains.
- [ ] Ensure required timestamps exist across persistent domain models.
- [ ] Keep room, participant, game, round, and word relationships aligned with the PRD and SDS.
- [ ] Seed a default word pack that allows room creation without manual bootstrap work.
- [ ] Cover model constraints and baseline word-pack behavior with tests.
- [ ] Verification gate: migrations apply cleanly and model/seed tests prove the baseline schema is valid for room creation and game bootstrap.

---

#### T-02: Room Entry HTTP Baseline
**Priority**: Critical  
**Phase**: P0 Existing Baseline  
**Depends On**: `T-01`  
**Impacts**: Guest entry path into rooms, initial host creation, and baseline room authorization flow  
**Blocks**: `T-05`, `T-06`, `T-21`, `T-22`

**Deliverables**:
- HTTP endpoints for room creation and joining
- Session-backed participant creation and same-session rejoin behavior
- Room-capacity enforcement for a maximum of 6 participants, including spectators
- Lobby-state and host-only start-game HTTP baseline
- Coverage for request validation and room membership authorization

- [ ] Provide room creation for guest users with `name`, `visibility`, and `display_name`.
- [ ] Provide room joining by `join_code` with Django-session participant ownership.
- [ ] Enforce the `6`-participant maximum for new room joins while still allowing same-session rejoin reuse.
- [ ] Reuse the same participant slot for same-session rejoin instead of duplicating room membership.
- [ ] Expose the room lobby-state read endpoint and the host-only start-game endpoint.
- [ ] Reject invalid room access, invalid payloads, and conflicting guest-session room assignments.
- [ ] Add HTTP tests for create, join, lobby access, and start-game authorization.
- [ ] Verification gate: guest create/join/start flows succeed or fail with correct status codes, including over-capacity rejection, and no partial data corruption.

---

#### T-03: Redis Room Runtime Helper Baseline
**Priority**: High  
**Phase**: P0 Existing Baseline  
**Depends On**: `T-01`  
**Impacts**: Shared room runtime primitives for presence and drawing-state recovery  
**Blocks**: `T-09`, `T-10`, `T-11`, `T-12`

**Deliverables**:
- Redis helper module for room presence keys
- Redis helper module for latest canvas snapshot storage
- TTL policy for transient room runtime keys

- [ ] Define the Redis key layout for room presence and latest canvas snapshot.
- [ ] Provide helper operations for add/remove/get/clear presence.
- [ ] Provide helper operations for set/get/clear canvas snapshot.
- [ ] Keep Redis helpers dependency-light and testable without a full running app.
- [ ] Add helper tests covering isolation, overwrite behavior, and TTL handling.
- [ ] Verification gate: fakeredis-backed tests prove helper behavior and room isolation.

---

#### T-04: Game Bootstrap & Basic Guess Service Baseline
**Priority**: Critical  
**Phase**: P0 Existing Baseline  
**Depends On**: `T-01`, `T-02`  
**Impacts**: First game start path, first-round creation, and current guess-service baseline that later tickets extend  
**Blocks**: `T-11`, `T-13`, `T-14`, `T-16`

**Deliverables**:
- Game start service for room snapshot and first round
- Initial word snapshot behavior
- Basic guess persistence and round resolution service baseline

- [ ] Start a game only from valid lobby state with enough eligible participants.
- [ ] Snapshot the selected room word pack into game-scoped words.
- [ ] Create the first round and first drawer from eligible room participants.
- [ ] Persist guess submissions through the existing service layer.
- [ ] Keep tests that prove current baseline behavior before later tickets intentionally replace parts of it.
- [ ] Verification gate: service tests prove game bootstrap and basic guess persistence work on the current baseline.

## 2. Rooms & Lobby Backend

Scope: room discovery, lobby configuration, membership state transitions, and empty-room lifecycle.

---

#### T-05: Public Room Directory API
**Priority**: Medium  
**Phase**: P1 Realtime Lobby  
**Depends On**: `T-02`  
**Impacts**: Public room discovery and entry into shareable rooms  
**Blocks**: `T-21`

**Deliverables**:
- HTTP endpoint or view payload for listing public rooms
- Filtering rules for public vs private visibility
- Inclusion of in-progress public rooms in the listing

- [ ] Expose a public-room listing surface that returns only public rooms.
- [ ] Include public rooms even when a game is already in progress, per the PRD.
- [ ] Exclude private rooms from room discovery surfaces.
- [ ] Return enough room metadata for the frontend entry page to render room cards or rows.
- [ ] Add HTTP tests for room visibility filtering and listing behavior.
- [ ] Verification gate: public room list exposes the correct rooms and never leaks private rooms.

---

#### T-06: Lobby Settings Update API
**Priority**: Medium  
**Phase**: P1 Realtime Lobby  
**Depends On**: `T-02`  
**Impacts**: Host-controlled lobby setup before game start  
**Blocks**: `T-22`

**Deliverables**:
- Host-only room settings update endpoint or action
- Validation for `name` and `visibility`
- Lobby-only edit enforcement

- [ ] Allow only the current host to update room `name` and `visibility`.
- [ ] Reject room setting edits when the room is not in `lobby`.
- [ ] Keep word-pack selection out of this MVP surface.
- [ ] Return an updated room state payload suitable for live lobby refresh.
- [ ] Add tests for host authorization, payload validation, and lobby-only restrictions.
- [ ] Verification gate: room settings can only be changed by the host while the room is in `lobby`.

---

#### T-07: Host Reassignment & Participant Connection Lifecycle
**Priority**: Critical  
**Phase**: P1 Realtime Lobby  
**Depends On**: `T-02`  
**Impacts**: Stable host ownership and participant state management across disconnects and leaves  
**Blocks**: `T-08`, `T-10`, `T-18`, `T-20`

**Deliverables**:
- Service logic for participant connect/disconnect transitions
- Host reassignment logic when the current host leaves
- Persistent participant state updates for live room transitions

- [ ] Define how participant connection state changes on room connect, disconnect, leave, and reconnect.
- [ ] Reassign the host randomly when the current host leaves and other participants remain.
- [ ] Clear host ownership when no participants remain in the room.
- [ ] Keep participant state transitions compatible with session-based room ownership.
- [ ] Add tests for host handoff and participant connection lifecycle changes.
- [ ] Verification gate: host reassignment and participant connection-state transitions behave deterministically and leave the room in a valid state.

---

#### T-08: Empty-Room Grace Lifecycle
**Priority**: High  
**Phase**: P1 Realtime Lobby  
**Depends On**: `T-03`, `T-07`  
**Impacts**: Room cleanup, room resurrection during grace, and long-lived room hygiene  
**Blocks**: `T-10`, `T-20`, `T-26`

**Deliverables**:
- Empty-room grace logic for `10`-minute retention
- Durable `empty_since` handling plus cleanup runtime timing
- Hard-delete flow when grace expires

- [ ] Move a room into `empty_grace` when it becomes empty.
- [ ] Persist the empty-room timestamp so cleanup survives process restarts.
- [ ] Clear empty-room state if someone rejoins before the deadline.
- [ ] Hard-delete the room when the grace deadline expires with no rejoin.
- [ ] Add tests for enter-grace, rejoin-before-expiry, and delete-after-expiry behavior.
- [ ] Verification gate: empty rooms clean up correctly without deleting rooms that were rejoined in time.

## 3. Realtime & Redis Runtime State

Scope: WebSocket entry, room broadcast behavior, Redis runtime state for rounds, and drawing synchronization.

---

#### T-09: Room WebSocket Consumer & Session Authorization
**Priority**: Critical  
**Phase**: P1 Realtime Lobby  
**Depends On**: `T-02`, `T-03`  
**Impacts**: Single-room live communication channel for all realtime MVP behavior  
**Blocks**: `T-10`, `T-11`, `T-12`, `T-22`, `T-23`, `T-24`

**Deliverables**:
- Project websocket routing for room sockets
- Room-scoped Channels consumer
- Session-based authorization for room socket access

- [ ] Add websocket routing for one room-scoped socket per room.
- [ ] Authorize room-socket connections by Django session membership in that room.
- [ ] Treat the room socket as authorization for existing room members only and do not allow it to bypass HTTP room-entry or capacity rules.
- [ ] Join connected participants to a room broadcast group.
- [ ] Update persistent and Redis presence state on connect and disconnect.
- [ ] Add consumer tests for authorization, connect, disconnect, and group membership behavior.
- [ ] Verification gate: only valid room participants can connect to the room socket and their presence state is updated correctly.

---

#### T-10: Live Lobby State Broadcast
**Priority**: Critical  
**Phase**: P1 Realtime Lobby  
**Depends On**: `T-07`, `T-08`, `T-09`  
**Impacts**: Live room-state rendering for the lobby and later gameplay shell  
**Blocks**: `T-22`, `T-23`

**Deliverables**:
- `room.state` server event payload
- Host-change and participant-status live broadcast behavior
- Initial room-state snapshot for newly connected clients

- [ ] Broadcast room-state updates when participants join, leave, disconnect, reconnect, or when host ownership changes.
- [ ] Include enough room metadata for live lobby rendering.
- [ ] Send an initial room-state snapshot on successful socket connect.
- [ ] Keep room-state payloads server-authoritative so the client only renders.
- [ ] Add consumer tests for room-state and host-change broadcasts.
- [ ] Verification gate: two connected participants see the same authoritative lobby state updates in realtime.

---

#### T-11: Redis Round Runtime State
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `T-03`, `T-04`, `T-09`  
**Impacts**: Server-owned runtime state for timers, eligible drawers, active-round guess state, and cleanup deadlines  
**Blocks**: `T-12`, `T-13`, `T-14`, `T-16`, `T-18`, `T-19`, `T-20`

**Deliverables**:
- Redis helpers or service layer for active turn runtime state
- Redis helpers or service layer for current-game remaining drawer state
- Redis helpers or service layer for per-round live guess state
- Role-specific round payload support for full drawer word vs masked or partial guesser word
- Cleanup deadline runtime state

- [ ] Define the logical Redis runtime layout for current turn, current cycle, current round guess-state, and cleanup deadlines.
- [ ] Store runtime values with room-scoped keys and bounded lifetime.
- [ ] Keep runtime state recoverable enough that durable game rows remain the long-lived source of truth.
- [ ] Add room-scoped round payload support so the drawer receives the full selected word while non-drawers receive only masked or partial word information.
- [ ] Expose helper functions or a service interface that higher-level game logic can call without duplicating Redis key knowledge.
- [ ] Add Redis/helper tests for turn, cycle, guess-state, and cleanup-key behavior.
- [ ] Verification gate: runtime state operations are isolated per room and support the later turn/guess lifecycle work.

---

#### T-12: Drawing Event Broadcast & Canvas Snapshot Sync
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `T-09`, `T-11`  
**Impacts**: Core live drawing visibility and reconnect drawing recovery  
**Blocks**: `T-24`, `T-25`

**Deliverables**:
- Drawer-authorized `drawing.stroke`, `drawing.end_stroke`, and `drawing.clear` socket events
- Drawing broadcast to room participants
- Latest canvas snapshot persistence in Redis
- Reconnect snapshot replay behavior

- [ ] Accept `drawing.stroke`, `drawing.end_stroke`, and `drawing.clear` only from the active drawer.
- [ ] Broadcast live `drawing.stroke`, `drawing.end_stroke`, and `drawing.clear` events to other room participants through the room socket.
- [ ] Persist the latest canvas snapshot in Redis for reconnect sync.
- [ ] Reset or refresh the stored canvas snapshot correctly when clear events occur.
- [ ] Replay the latest canvas snapshot to newly connected or reconnecting participants in the active round.
- [ ] Add consumer tests for drawer-only drawing authorization and snapshot replay behavior.
- [ ] Verification gate: reconnecting clients can recover the current drawing state without replaying historical room data from MySQL.

## 4. Gameplay Flow & Scoring Backend

Scope: round timing, drawer rotation, guess evaluation, scoring, reconnect gameplay rules, and full game completion flow.

---

#### T-13: Round Timer & Early-Finish Coordinator
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `T-04`, `T-11`  
**Impacts**: Server-controlled round duration, timer events, and round-end triggers  
**Blocks**: `T-14`, `T-15`, `T-16`, `T-19`, `T-20`, `T-23`

**Deliverables**:
- Server-side round timer orchestration for `90`-second rounds
- Runtime deadline tracking for active rounds
- Server-side `10`-second between-turn intermission timing and broadcast behavior
- Early-finish logic when all eligible non-drawer guessers are correct

- [ ] Add server-side round timing using runtime state instead of persisted ticking timer records.
- [ ] Publish authoritative timer updates to connected clients.
- [ ] End the round when the timer expires.
- [ ] Manage the `10`-second between-turn intermission as a server-owned phase rather than a client-side delay.
- [ ] Broadcast intermission countdown state safely so all clients transition to the next round in sync.
- [ ] End the round early when all eligible non-drawer guessers are already correct.
- [ ] Add tests for timer expiry and all-guessers-correct early completion behavior.
- [ ] Verification gate: round completion is driven by the server and not by client clocks.

---

#### T-14: Full Game Cycle Drawer Rotation & Word Uniqueness
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `T-04`, `T-11`, `T-13`  
**Impacts**: Multi-round gameplay continuity and one-drawer-per-game enforcement  
**Blocks**: `T-18`, `T-19`, `T-20`

**Deliverables**:
- Remaining eligible drawer pool for the active game
- Unique word selection within the game snapshot
- Role-specific round-start payload generation for drawer and non-drawer participants
- Next-round creation after round completion

- [ ] Track which eligible participants still need to draw in the current game.
- [ ] Ensure a participant is not selected twice as drawer in the same game.
- [ ] Ensure the same word is not selected twice in the same game.
- [ ] Generate role-specific round-start payloads so the drawer receives the full word while non-drawers receive only masked or partial word information.
- [ ] Create the next round automatically after the between-turn countdown when the game still has remaining drawers.
- [ ] Add service tests for drawer rotation and word uniqueness across multiple rounds.
- [ ] Verification gate: a full game can progress through multiple unique drawers and unique words without manual intervention.

---

#### T-15: Guess Submission Pipeline Over Room Socket
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `T-09`, `T-13`, `T-04`  
**Impacts**: Live guess entry into the server-authoritative gameplay loop  
**Blocks**: `T-16`, `T-17`, `T-23`

**Deliverables**:
- `guess.submit` client-to-server event handling
- Round-bound guess persistence
- `guess.result` server-to-client event handling

- [ ] Accept live guess submissions through the room socket.
- [ ] Validate that the submitting participant belongs to the room and is eligible to guess in the current round.
- [ ] Persist guesses on the active round while the round is live.
- [ ] Broadcast normalized guess-result payloads back to room participants.
- [ ] Add consumer and service tests for valid submissions, invalid submissions, and result broadcasts.
- [ ] Verification gate: live guess submission works over the room socket and results are server-evaluated, not client-derived.

---

#### T-16: Time-Based Scoring & Multi-Guesser Round Resolution
**Priority**: Critical  
**Phase**: P3 Full Game Rules  
**Depends On**: `T-13`, `T-14`, `T-15`  
**Impacts**: Replacement of the current simple placeholder scoring model with the target PRD/SDS scoring behavior  
**Blocks**: `T-20`, `T-25`

**Deliverables**:
- Bounded linear guesser score formula
- Bounded linear drawer bonus formula
- Multi-guesser score accumulation while a round remains active

- [ ] Replace the current `1`-point guesser and `1`-point drawer placeholder scoring behavior.
- [ ] Implement guesser scoring from `100` down to `20` based on remaining round time.
- [ ] Implement drawer bonus from `50` down to `10` for each distinct correct guesser.
- [ ] Keep the round active after one correct guess unless another round-end rule triggers.
- [ ] Add service tests that prove multiple players can score correctly in the same round at different times.
- [ ] Verification gate: score calculations match the SDS formulas and the round no longer ends on the first correct guess.

---

#### T-17: Near-Match, Duplicate, And Correct-Once Guess Rules
**Priority**: High  
**Phase**: P3 Full Game Rules  
**Depends On**: `T-11`, `T-15`  
**Impacts**: Guess-quality feedback and per-player guess-state correctness  
**Blocks**: `T-25`

**Deliverables**:
- Same-player duplicate handling
- Near-match handling for multi-word and single-word targets
- Ignore-for-scoring behavior for players already correct in the round

- [ ] Treat same-player repeated normalized guesses as `duplicate`.
- [ ] Keep duplicate handling player-scoped rather than global across the room.
- [ ] Implement `near_match` for multi-word targets when the guess exactly matches one target word.
- [ ] Implement stricter single-word `near_match` using a prefix or stem-style rule rather than loose fuzzy matching.
- [ ] Ignore later guesses from a player who is already correct for that round when scoring and round-completion state are computed.
- [ ] Add tests covering `correct`, `incorrect`, `near_match`, `duplicate`, and already-correct repeat-guess behavior.
- [ ] Verification gate: guess outcomes match the PRD/SDS outcome rules consistently across edge cases.

---

#### T-18: Mid-Game Joiners, Spectators, And Reconnect Reclaim
**Priority**: Critical  
**Phase**: P3 Full Game Rules  
**Depends On**: `T-07`, `T-11`, `T-14`  
**Impacts**: Correct participant eligibility rules during active games and reconnect continuity  
**Blocks**: `T-20`, `T-25`

**Deliverables**:
- Mid-game join spectator handling
- Next-turn eligibility inclusion for mid-game joiners
- Reconnect reclaim flow for non-drawer participants

- [ ] Mark mid-game joiners as spectators for the current turn.
- [ ] Prevent current-turn guessing by participants who joined after the round started.
- [ ] Add valid mid-game joiners into the remaining eligible drawer pool on the next turn.
- [ ] Restore the same participant row and score when a non-drawer reconnects during the same game.
- [ ] Add service and consumer tests for spectator restrictions and reconnect reclaim behavior.
- [ ] Verification gate: joining or reconnecting mid-game preserves fairness and does not create duplicate participants or score loss.

---

#### T-19: Drawer Disconnect Grace & Turn Outcome Handling
**Priority**: Critical  
**Phase**: P3 Full Game Rules  
**Depends On**: `T-11`, `T-13`, `T-14`  
**Impacts**: Reliable handling of drawer disconnects without immediate round collapse  
**Blocks**: `T-20`, `T-25`

**Deliverables**:
- Drawer disconnect grace timer
- Resume-or-end decision logic for disconnected drawers
- `drawer_disconnected` round terminal outcome

- [ ] Start a `15`-second grace deadline when the active drawer disconnects.
- [ ] Resume the round if the drawer reconnects before the deadline.
- [ ] End the round as `drawer_disconnected` if the grace deadline expires first.
- [ ] Broadcast the resulting turn-state updates to room participants.
- [ ] Add service and consumer tests for reconnect-before-deadline and expiry-without-reconnect scenarios.
- [ ] Verification gate: drawer disconnect handling behaves consistently and produces the correct terminal round outcome.

---

#### T-20: Game Finish, Leaderboard Cooldown, And Auto-Restart
**Priority**: Critical  
**Phase**: P3 Full Game Rules  
**Depends On**: `T-14`, `T-16`, `T-18`, `T-19`  
**Impacts**: Complete end-to-end game loop and replayability inside a room  
**Blocks**: `T-25`, `T-26`

**Deliverables**:
- Game completion detection
- `20`-second leaderboard state
- Automatic next-game start when players remain
- Score reset between games

- [ ] Finish a game when the remaining eligible drawer pool is exhausted.
- [ ] Cancel a game if all players leave during active play.
- [ ] Broadcast a leaderboard state for `20` seconds after game finish.
- [ ] Automatically start a fresh game with reset scores if players remain after the leaderboard cooldown.
- [ ] Add service and integration tests for finish, cancel, leaderboard, and auto-restart behavior.
- [ ] Verification gate: one room can move from lobby through a completed game and into the next game automatically.

## 5. Frontend UI & Game Screens

Scope: server-rendered entry, lobby, and gameplay pages with vanilla JavaScript room-socket behavior.

---

#### T-21: Entry Screen & Public Room Discovery UI
**Priority**: Medium  
**Phase**: P1 Realtime Lobby  
**Depends On**: `T-02`, `T-05`  
**Impacts**: User-facing room entry and public-room discovery  
**Blocks**: `T-22`

**Deliverables**:
- Landing or entry page template
- Create-room and join-room form UI
- Public room list rendering

- [ ] Add a server-rendered entry page for create-room and join-room flows.
- [ ] Render public-room discovery data from the public-room listing surface.
- [ ] Keep the MVP UI simple and functional rather than over-designed.
- [ ] Handle successful room creation or join by redirecting into the room page.
- [ ] Add UI or view tests for entry-page rendering and form submission behavior where practical.
- [ ] Verification gate: a guest can enter the app in the browser and reach a room without using raw JSON endpoints manually.

---

#### T-22: Live Lobby Page Template & Room Client
**Priority**: Critical  
**Phase**: P1 Realtime Lobby  
**Depends On**: `T-06`, `T-09`, `T-10`, `T-21`  
**Impacts**: Playable live lobby and host-controlled start flow  
**Blocks**: `T-23`, `T-24`

**Deliverables**:
- Room page template for the lobby state
- Vanilla JS room client for socket connect and room-state rendering
- Derived join URL display and copy control for room sharing
- Host start-game and lobby-settings controls

- [ ] Render room metadata, host indicator, participant list, and room status on the room page.
- [ ] Connect the page to the room socket and update the lobby UI from server-sent room-state events.
- [ ] Show the derived join URL on the room page and provide a simple copy action for sharing private or public room links.
- [ ] Show host-only controls for start-game and lobby settings updates.
- [ ] Show non-host participants a correct read-only view.
- [ ] Add frontend and HTTP tests for room-page access and key lobby rendering behavior where practical.
- [ ] Verification gate: multiple browser sessions can stay in sync inside the live lobby and the host can start the game from the page.

---

#### T-23: Gameplay Page Shell, HUD, And Guess UI
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `T-10`, `T-13`, `T-14`, `T-15`, `T-22`  
**Impacts**: Playable non-drawer gameplay view and server-driven score/timer visibility  
**Blocks**: `T-25`

**Deliverables**:
- Gameplay room page state on the existing room page or a game-mode section within it
- HUD for timer, participants, scores, and round state
- Guess input and guess-result rendering
- Leaderboard and between-turn UI states

- [ ] Render round timer, participant scores, and current game state from server-owned events.
- [ ] Provide guess input for eligible non-drawer participants.
- [ ] Render masked or partial word information appropriately for non-drawers.
- [ ] Render server-provided guess-result states generically so later backend additions such as near-match, duplicate, leaderboard, and auto-restart can plug into the same UI surface without redesign.
- [ ] Show leaderboard and between-turn states based on server events rather than local guesses.
- [ ] Keep drawer and non-drawer page behavior role-aware.
- [ ] Add frontend tests for guess-input state and timer/scoreboard rendering where practical.
- [ ] Verification gate: a non-drawer can watch the timer, submit guesses, see results, and follow round-to-round progression from the browser.

---

#### T-24: Browser Drawing Surface & Room Socket Integration
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `T-12`, `T-22`  
**Impacts**: Playable live drawing experience for the active drawer and synchronized viewing for everyone else  
**Blocks**: `T-25`

**Deliverables**:
- Browser drawing surface for the active drawer
- Vanilla JS drawing client integrated with the room socket
- Browser handling for `drawing.stroke`, `drawing.end_stroke`, and `drawing.clear`
- Viewer-side drawing replay and reconnect snapshot rendering

- [ ] Render a drawing surface on the gameplay page for the active drawer.
- [ ] Send `drawing.stroke`, `drawing.end_stroke`, and `drawing.clear` over the room socket in the format expected by the backend.
- [ ] Render inbound `drawing.stroke`, `drawing.end_stroke`, and `drawing.clear` events for non-drawer viewers.
- [ ] Restore the current drawing snapshot when a participant reconnects or opens the page mid-round.
- [ ] Prevent non-drawers from sending drawing events through the UI.
- [ ] Add frontend tests for role-aware drawing UI behavior where practical.
- [ ] Verification gate: the active drawer can draw live and all other participants can see the drawing update in the browser.

## 6. Quality Assurance & MVP Hardening

Scope: late cross-cutting verification, stabilization, and shared-doc updates after the main MVP loop is implemented.

---

#### T-25: End-To-End Multiplayer Verification & Bug Sweep
**Priority**: Critical  
**Phase**: P4 Hardening & QA  
**Depends On**: `T-12`, `T-16`, `T-17`, `T-19`, `T-20`, `T-23`, `T-24`  
**Impacts**: Confidence that the MVP works as one system instead of isolated features  
**Blocks**: `T-26`

**Deliverables**:
- Integration and consumer test coverage for the core multiplayer loop
- Bug-fix pass driven by real cross-browser or multi-client verification
- MVP verification checklist derived from the PRD and SDS

- [ ] Add or complete multi-client integration tests that cover room entry, lobby sync, gameplay, scoring, and next-game progression.
- [ ] Add consumer tests for the core room-socket event families.
- [ ] Run a bug-fix pass against issues found during full MVP verification.
- [ ] Verify drawer/non-drawer role behavior, reconnect handling, and room cleanup flows.
- [ ] Record any rule changes discovered during QA back into the planning docs before finalizing ticket splits.
- [ ] Verification gate: the team can run a reproducible end-to-end MVP verification pass without relying on ad hoc manual reasoning.

---

#### T-26: Shared Context Refresh & Final MVP Hardening
**Priority**: Medium  
**Phase**: P4 Hardening & QA  
**Depends On**: `T-08`, `T-20`, `T-25`  
**Impacts**: Final planning/documentation consistency and reduced confusion for the team after implementation  
**Blocks**: None

**Deliverables**:
- Refreshed implementation-state documentation
- Final hardening fixes for cleanup, configuration, or edge-case regressions discovered late
- Final planning-ready state for track split and issue audit

- [ ] Update shared planning context if implementation reality has diverged from stale progress notes.
- [ ] Resolve final hardening issues that are too cross-cutting to belong to one earlier feature ticket.
- [ ] Make sure room cleanup, host reassignment, reconnect, and game-loop edge cases are still consistent after QA fixes.
- [ ] Leave the codebase and planning docs in a state where issue-audit and track split can proceed cleanly.
- [ ] Verification gate: shared docs and the MVP baseline reflect reality closely enough that the final tracker and per-track files can be generated without guessing.
