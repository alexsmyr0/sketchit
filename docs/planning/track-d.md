# Track D — Duo Draw Co-op Mode

Source backlog: `docs/planning/tickets-by-topic.md`

> Scope: An additional room game mode in which two participants share the same canvas, see the same target word, and coordinate over a drawer-only private chat while non-drawers continue to guess against the shared canvas. This track owns the persistent room mode setting, lobby mode selection surface, duo drawer-pair selection, shared-canvas drawing authority, drawer-only co-chat, duo-aware scoring and disconnect handling, and the browser experience for both drawers and guessers when a room runs in duo mode.

## Phase Order

- **P0 Existing Baseline**: No new Track D tickets
- **P1 Realtime Lobby**: `D-01` to `D-02`
- **P2 Playable Round Loop**: `D-03` to `D-05`
- **P3 Full Game Rules**: `D-06` to `D-07`
- **P4 Hardening & QA**: `D-08` to `D-09`

---

#### D-01: Room Game Mode Persistence
**Priority**: High  
**Phase**: P1 Realtime Lobby  
**Depends On**: `A-01`  
**Impacts**: Persistent room configuration for which drawing mode the room is in (`normal` vs `duo`)  
**Blocks**: `D-02`, `D-03`, `D-08`

**Deliverables**:
- New persistent `game_mode` field on `Room` with allowed values `normal` and `duo`
- MySQL migration backfilling existing rows to `normal`
- Game-time snapshot of the room's mode onto the `Game` row so mid-game lobby edits cannot affect an active game
- Model-layer validation rejecting unknown modes

- [x] Add `game_mode` CharField on `Room` with choices `normal` and `duo` defaulting to `normal`.
- [x] Ship the migration adding the field and backfilling existing rows.
- [x] Snapshot the selected mode onto the `Game` row at game start so the active game is decoupled from later room edits.
- [x] Reject invalid mode values at the model layer.
- [x] Cover model defaults, choice validation, and the `Game` snapshot behavior with tests.
- [x] Verification gate: migrations apply cleanly and a started game records the room's mode independent of subsequent room edits.

---

#### D-02: Lobby Mode Selection API & Settings Surface
**Priority**: High  
**Phase**: P1 Realtime Lobby  
**Depends On**: `A-03`, `D-01`  
**Impacts**: Host-controlled lobby mode selection prior to game start and live propagation of the selected mode  
**Blocks**: `D-03`, `D-08`

**Deliverables**:
- `game_mode` accepted by the existing host-only lobby settings update endpoint
- Server-side validation against the allowed mode set
- Lobby-only edit enforcement
- `game_mode` exposed on lobby-state read responses and `room.state` broadcasts

- [ ] Accept `game_mode` in the host-only lobby settings update endpoint or action.
- [ ] Reject unknown mode values with a `400` and a clear error code.
- [ ] Reject mode edits when the room is not in `lobby` status.
- [ ] Include `game_mode` in lobby-state read responses.
- [ ] Include `game_mode` in `room.state` socket broadcasts so connected lobby participants see the change without reloading.
- [ ] Add tests for host authorization, payload validation, lobby-only restriction, and live broadcast inclusion.
- [ ] Verification gate: only the host can change the mode in the lobby and all connected lobby participants observe the change live.

---

#### D-03: Duo Drawer Pair Selection & Round Schema
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `K-04`, `D-01`  
**Impacts**: Round drawer selection, eligible-drawer-pool consumption, and round persistence when the active game runs in duo mode  
**Blocks**: `D-04`, `D-05`, `D-06`, `D-07`

**Deliverables**:
- `second_drawer_participant` and `second_drawer_nickname` fields on `Round`
- Migration adding the new round fields
- Pair selection logic that picks two distinct participants per duo round from the remaining eligible drawer pool and removes both from the pool
- Odd-count fallback that runs a final single-drawer turn and ends the game when only one eligible drawer remains
- Role-specific round-start payloads that name both drawers for duo rounds

- [x] Add `second_drawer_participant` and `second_drawer_nickname` to the `Round` model with nullable defaults so normal-mode rounds are unaffected.
- [x] Ship the migration adding the new round fields.
- [x] Update drawer pool consumption so a duo round removes both selected drawers from the remaining pool at round start.
- [x] Fall back to a single-drawer turn when the remaining pool only has one eligible drawer left and end the game after that turn.
- [x] Keep mid-game joiner eligibility rules consistent with single-drawer turn rules for the current and following round.
- [x] Extend role-specific round-start payloads so both drawers receive the full word while non-drawers receive only the masked or partial word information.
- [x] Cover pair selection, pool consumption, odd-count fallback, and dual role-payload behavior with service tests.
- [x] Verification gate: a duo game cycles through paired drawers, never repeats a participant as a drawer within the same game, and ends cleanly when the pool runs out.

---

#### D-04: Shared Canvas For Drawer Pair
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `N-04`, `D-03`  
**Impacts**: Drawing event authority and canvas snapshot semantics for duo rounds  
**Blocks**: `D-08`, `D-09`

**Deliverables**:
- Server accepts `drawing.stroke`, `drawing.end_stroke`, and `drawing.clear` from either drawer in a duo round
- Single shared room-scoped canvas snapshot for reconnect sync (no per-drawer canvas)
- Continued rejection of drawing events from guessers and spectators

- [ ] Authorize drawing events from any participant whose ID matches the current round's drawer or second drawer.
- [ ] Continue rejecting drawing events from guessers, spectators, and disconnected participants.
- [ ] Broadcast drawing events to all connected room participants without distinguishing between primary and secondary drawer.
- [ ] Keep the canvas snapshot key room-scoped so the existing reconnect replay path works unchanged.
- [ ] Add consumer tests covering accept-from-either-drawer, reject-from-non-drawer, and broadcast fan-out in a duo round.
- [ ] Verification gate: both drawers draw on the same canvas and every viewer sees the merged result without flicker or duplicate strokes.

---

#### D-05: Drawer Pair Private Chat
**Priority**: High  
**Phase**: P2 Playable Round Loop  
**Depends On**: `N-02`, `D-03`  
**Impacts**: Private coordination channel between paired drawers during a duo round  
**Blocks**: `D-08`, `D-09`

**Deliverables**:
- New socket event family `cochat.message` for drawer-to-drawer chat
- Server-side authorization restricting sending and receiving to the two current drawers
- Hard rejection of `cochat.message` traffic in `normal`-mode rounds
- Runtime-only chat state with no persistence and no inclusion in canvas snapshots

- [x] Accept `cochat.message` only from participants who are the current drawer or second drawer for the active duo round.
- [x] Route accepted messages only to the other drawer in the pair; the sender may echo locally without involving the server.
- [x] Reject `cochat.message` submissions from guessers, spectators, and disconnected drawers with a server-side error event.
- [x] Reject `cochat.message` submissions in `normal`-mode rounds with the same error event.
- [x] Keep chat content out of any persistent storage and out of the canvas snapshot.
- [x] Clear any in-memory co-chat state when the round or game ends.
- [x] Add consumer tests for routing isolation, non-drawer rejection, normal-mode rejection, and round-end cleanup.
- [x] Verification gate: the drawer pair can chat live and no guesser client ever receives a `cochat.message` event.

---

#### D-06: Scoring Adaptation For Duo Rounds
**Priority**: High  
**Phase**: P3 Full Game Rules  
**Depends On**: `K-05`, `D-03`  
**Impacts**: Drawer bonus distribution and eligible-guesser-set computation for duo rounds  
**Blocks**: `D-09`

**Deliverables**:
- Both drawers credited per correct guess in a duo round using a split drawer bonus
- Per-drawer bonus value set to `round(drawer_bonus / 2)` so the total drawer payout per correct guess remains within `1` point of the single-drawer formula due to rounding
- Self-guess prevention extended to both drawers in a duo round
- Eligible non-drawer guesser set updated to exclude both drawers

- [ ] On every correct guess in a duo round, credit both drawers using the split-bonus formula `round(drawer_bonus / 2)`.
- [ ] Prevent either drawer in a duo round from guessing their own word for score.
- [ ] Exclude both drawers from the eligible non-drawer guesser set used by early-finish checks.
- [ ] Keep `guesser_points` unchanged for correct non-drawer guessers.
- [ ] Keep score totals on the participant record consistent with the existing single-drawer model.
- [ ] Add service tests for split-bonus payouts, drawer self-guess rejection for both drawers, and eligible guesser set integrity.
- [ ] Verification gate: duo round scoring is deterministic and the total drawer payout per correct guess is within `1` point of the single-drawer formula.

---

#### D-07: Duo Drawer Disconnect Handling
**Priority**: High  
**Phase**: P3 Full Game Rules  
**Depends On**: `K-07`, `D-03`, `D-06`  
**Impacts**: Round continuity and terminal outcome rules when one or both drawers disconnect in a duo round  
**Blocks**: `D-09`

**Deliverables**:
- Duo round remains active when only one of the two drawers disconnects
- Standard `15`-second drawer grace timer fires only when both drawers are disconnected at the same time
- Reconnect during the dual-disconnect grace window resumes the round
- Drawer-bonus split formula behavior unchanged during single-drawer continuation

- [ ] On a single drawer disconnect in duo mode, keep the round active and continue accepting drawing events from the remaining connected drawer.
- [ ] Pause co-chat participation for the disconnected drawer until reconnect; the remaining drawer's local UI is not affected.
- [ ] Start the `15`-second drawer grace timer only when both drawers are disconnected at the same time.
- [ ] On reconnect by either drawer during the grace window, clear the timer and resume the round.
- [ ] If the grace timer expires with both drawers still disconnected, end the round with outcome `drawer_disconnected`.
- [ ] Keep scoring behavior consistent: the surviving drawer still receives only the split-bonus portion even while the other is disconnected.
- [ ] Add service tests for single-disconnect continuation, dual-disconnect grace, reconnect resume, and grace-expiry round end.
- [ ] Verification gate: a duo round survives a single drawer leaving but ends cleanly if both drawers stay disconnected past the grace deadline.

---

#### D-08: Duo Mode Browser Experience
**Priority**: High  
**Phase**: P4 Hardening & QA  
**Depends On**: `G-02`, `G-03`, `G-04`, `D-02`, `D-04`, `D-05`  
**Impacts**: Lobby mode dropdown, gameplay HUD for paired drawers, drawer-only co-chat panel, and shared-canvas input behavior  
**Blocks**: `D-09`

**Deliverables**:
- Host-only `game_mode` dropdown in the lobby settings UI with `normal` and `duo` options
- Live lobby rendering of the currently selected mode for all participants
- Gameplay HUD that labels both drawers as active during a duo round
- Drawer-only co-chat panel rendered only for participants who are one of the two current drawers
- Shared canvas surface that accepts input from the local client whenever it is either of the two current drawers

- [x] Add a host-only `game_mode` dropdown to the lobby settings UI with `normal` and `duo` options.
- [x] Disable the dropdown for non-host participants and outside `lobby` status.
- [x] Render the currently selected mode for all participants in the lobby and update it live on `room.state` events.
- [x] In duo rounds, label both drawers in the gameplay HUD and treat both as active drawing roles in the UI.
- [x] Render the drawer-only co-chat panel only for participants who are either current drawer in a duo round and hide it for everyone else.
- [x] Wire client-side `cochat.message` send and receive over the existing room socket.
- [x] Ensure the canvas surface accepts local input whenever the local client is one of the two current drawers.
- [x] Add front-end coverage for the lobby dropdown gating rule and the co-chat panel visibility rule.
- [ ] Verification gate: in a live test session, two drawers see and use the co-chat panel and shared canvas while guessers see only the shared canvas and never the chat.

---

#### D-09: Duo Mode End-To-End Verification
**Priority**: High  
**Phase**: P4 Hardening & QA  
**Depends On**: `D-04`, `D-05`, `D-06`, `D-07`, `D-08`  
**Impacts**: Final integration sweep for the duo-mode feature across lobby, runtime, scoring, disconnect, and browser layers  
**Blocks**: None

**Deliverables**:
- Documented multi-client manual verification of the full duo-mode flow
- Automated integration coverage for at least one baseline end-to-end duo round
- Bug-fix sweep for issues uncovered during multi-client runs

- [ ] Run a multi-client session in which the host switches the lobby mode to `duo`, starts a game, and both drawers complete a duo round together.
- [ ] Verify guessers never receive `cochat.message` payloads during the duo round.
- [ ] Verify guess scoring follows the duo-mode split-bonus formula.
- [ ] Verify a single drawer disconnect leaves the round active and a dual disconnect ends it under the documented grace rule.
- [ ] Verify the mode reverts cleanly between games when the host toggles the lobby setting back to `normal`.
- [ ] Add an automated integration test for a baseline duo round including pair selection, shared canvas events, and correct scoring payouts.
- [ ] File and fix any blocking bugs uncovered during the manual run.
- [ ] Verification gate: a full duo game runs end-to-end across at least three clients with no blocking issues and scoring matches the documented formula.
