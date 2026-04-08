# Track K — Gameplay Rule Backend & Supporting APIs

Source backlog: `docs/planning/tickets-by-topic.md`

> Scope: Server-side gameplay orchestration, room discovery API support, round timing, drawer rotation, scoring, guess outcome rules, and drawer disconnect handling. This track should stay backend-focused and avoid owning Redis/socket transport or browser UI.

## Phase Order

- **P0 Existing Baseline**: `K-01`
- **P1 Realtime Lobby**: `K-02`
- **P2 Playable Round Loop**: `K-03` to `K-04`
- **P3 Full Game Rules**: `K-05` to `K-07`
- **P4 Hardening & QA**: No new Track K tickets

---

#### K-01: Game Bootstrap & Basic Guess Service Baseline
**Source Ticket**: `T-04`  
**Priority**: Critical  
**Phase**: P0 Existing Baseline  
**Depends On**: `A-01`, `A-02`  
**Impacts**: First game start path, first-round creation, and the baseline guess service that later tickets extend  
**Blocks**: `N-03`, `K-03`, `K-04`, `K-05`

**Deliverables**:
- Game start service for room snapshot and first round
- Initial word snapshot behavior
- Basic guess persistence and round resolution service baseline

- [ ] Start a game only from valid lobby state with enough eligible participants.
- [ ] Snapshot the selected room word pack into game-scoped words.
- [ ] Create the first round and first drawer from eligible room participants.
- [ ] Persist guess submissions through the service layer.
- [ ] Keep tests that prove current baseline behavior before later tickets intentionally replace parts of it.
- [ ] Verification gate: service tests prove game bootstrap and basic guess persistence work on the current baseline.

---

#### K-02: Public Room Directory API
**Source Ticket**: `T-05`  
**Priority**: Medium  
**Phase**: P1 Realtime Lobby  
**Depends On**: `A-02`  
**Impacts**: Public room discovery and entry into shareable rooms  
**Blocks**: `G-01`

**Deliverables**:
- HTTP endpoint or view payload for listing public rooms
- Filtering rules for public vs private visibility
- Inclusion of in-progress public rooms in the listing

- [ ] Expose a public-room listing surface that returns only public rooms.
- [ ] Include public rooms even when a game is already in progress, per the PRD.
- [ ] Exclude private rooms from room discovery surfaces.
- [ ] Return enough room metadata for the frontend entry page to render room cards or rows.
- [ ] Add HTTP tests for room visibility filtering and listing behavior.
- [ ] Verification gate: the public room list exposes the correct rooms and never leaks private rooms.

---

#### K-03: Round Timer & Early-Finish Coordinator
**Source Ticket**: `T-13`  
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `K-01`, `N-03`  
**Impacts**: Server-controlled round duration, timer events, intermission timing, and round-end triggers  
**Blocks**: `K-04`, `N-05`, `K-05`, `K-07`, `G-03`

**Deliverables**:
- Server-side round timer orchestration for `90`-second rounds
- Runtime deadline tracking for active rounds
- Server-side `10`-second between-turn intermission timing
- Early-finish logic when all eligible non-drawer guessers are correct

- [ ] Add server-side round timing using runtime state instead of persisted ticking timer rows.
- [ ] Publish authoritative timer updates to connected clients.
- [ ] End the round when the timer expires.
- [ ] Manage the `10`-second between-turn intermission as a server-owned phase rather than client delay.
- [ ] Broadcast intermission countdown state safely so all clients transition in sync.
- [ ] End the round early when all eligible non-drawer guessers are already correct.
- [ ] Keep disconnected guessers in the original eligible-guesser set so disconnect alone does not trigger early-finish.
- [ ] Add tests for timer expiry and all-guessers-correct early completion behavior.
- [ ] Verification gate: round completion is driven by the server and not by client clocks.

---

#### K-04: Full Game Cycle Drawer Rotation & Word Uniqueness
**Source Ticket**: `T-14`  
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `K-01`, `N-03`, `K-03`  
**Impacts**: Multi-round gameplay continuity and one-drawer-per-game enforcement  
**Blocks**: `A-07`, `K-07`, `A-08`

**Deliverables**:
- Remaining eligible drawer pool for the active game
- Unique word selection within the game snapshot
- Role-specific round-start payload generation
- Next-round creation after round completion

- [ ] Track which eligible participants still need to draw in the current game.
- [ ] Ensure a participant is not selected twice as drawer in the same game.
- [ ] Ensure the same word is not selected twice in the same game.
- [ ] Generate role-specific round-start payloads so the drawer receives the full word while non-drawers receive masked or partial word information.
- [ ] Create the next round automatically after the between-turn countdown when the game still has remaining drawers.
- [ ] Add service tests for drawer rotation and word uniqueness across multiple rounds.
- [ ] Verification gate: a full game can progress through multiple unique drawers and unique words without manual intervention.

---

#### K-05: Time-Based Scoring & Multi-Guesser Round Resolution
**Source Ticket**: `T-16`  
**Priority**: Critical  
**Phase**: P3 Full Game Rules  
**Depends On**: `K-03`, `K-04`, `N-05`  
**Impacts**: Replacement of the current placeholder scoring model with the target PRD/SDS scoring behavior  
**Blocks**: `A-08`, `G-05`

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

#### K-06: Near-Match, Duplicate, And Correct-Once Guess Rules
**Source Ticket**: `T-17`  
**Priority**: High  
**Phase**: P3 Full Game Rules  
**Depends On**: `N-03`, `N-05`  
**Impacts**: Guess-quality feedback and per-player guess-state correctness  
**Blocks**: `G-05`

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

#### K-07: Drawer Disconnect Grace & Turn Outcome Handling
**Source Ticket**: `T-19`  
**Priority**: Critical  
**Phase**: P3 Full Game Rules  
**Depends On**: `N-03`, `K-03`, `K-04`  
**Impacts**: Reliable handling of drawer disconnects without immediate round collapse  
**Blocks**: `A-08`, `G-05`

**Deliverables**:
- Drawer disconnect grace timer
- Resume-or-end decision logic for disconnected drawers
- `drawer_disconnected` round terminal outcome

- [ ] Start a `15`-second grace deadline when the active drawer disconnects.
- [ ] Resume the round if the drawer reconnects before the deadline.
- [ ] End the round as `drawer_disconnected` if the grace deadline expires first.
- [ ] Broadcast the resulting turn-state updates to room participants.
- [ ] Add service and consumer-facing tests for reconnect-before-deadline and expiry-without-reconnect scenarios.
- [ ] Verification gate: drawer disconnect handling behaves consistently and produces the correct terminal round outcome.
