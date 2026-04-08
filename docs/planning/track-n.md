# Track N — Realtime Runtime, Redis & Socket Transport

Source backlog: `docs/planning/tickets-by-topic.md`

> Scope: Redis runtime helpers, Channels room transport, active-round runtime state, drawing transport, and guess transport. This track owns the realtime backbone and should avoid taking on browser UI or persistent rule-heavy backend tickets that do not need Redis/socket ownership.

## Phase Order

- **P0 Existing Baseline**: `N-01`
- **P1 Realtime Lobby**: `N-02`
- **P2 Playable Round Loop**: `N-03` to `N-05`
- **P3 Full Game Rules**: No new Track N tickets
- **P4 Hardening & QA**: No new Track N tickets

---

#### N-01: Redis Room Runtime Helper Baseline
**Source Ticket**: `T-03`  
**Priority**: High  
**Phase**: P0 Existing Baseline  
**Depends On**: `A-01`  
**Impacts**: Shared room runtime primitives for presence and drawing-state recovery  
**Blocks**: `N-02`, `N-03`, `N-04`, `N-05`

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

#### N-02: Room WebSocket Consumer & Session Authorization
**Source Ticket**: `T-09`  
**Priority**: Critical  
**Phase**: P1 Realtime Lobby  
**Depends On**: `A-02`, `N-01`  
**Impacts**: Single-room live communication channel for all realtime MVP behavior  
**Blocks**: `N-03`, `N-04`, `N-05`, `G-02`, `G-03`, `G-04`

**Deliverables**:
- Project websocket routing for room sockets
- Room-scoped Channels consumer
- Session-based authorization for room socket access

- [ ] Add websocket routing for one room-scoped socket per room.
- [ ] Authorize room-socket connections by Django session membership in that room.
- [ ] Treat the room socket as authorization for existing room members only and do not allow it to bypass HTTP room entry or capacity rules.
- [ ] Join connected participants to a room broadcast group.
- [ ] Update persistent and Redis presence state on connect and disconnect.
- [ ] Add consumer tests for authorization, connect, disconnect, and group membership behavior.
- [ ] Verification gate: only valid room participants can connect to the room socket and their presence state is updated correctly.

---

#### N-03: Redis Round Runtime State
**Source Ticket**: `T-11`  
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `N-01`, `K-01`, `N-02`  
**Impacts**: Server-owned runtime state for timers, eligible drawers, active-round guess state, cleanup deadlines, and round payload support  
**Blocks**: `N-04`, `K-03`, `K-04`, `K-05`, `A-07`, `K-07`, `A-08`

**Deliverables**:
- Redis helpers or service layer for active turn runtime state
- Redis helpers or service layer for current-game remaining drawer state
- Redis helpers or service layer for per-round live guess state
- Redis-side role-specific round payload support for full drawer word vs masked or partial guesser word
- Cleanup deadline runtime state

- [ ] Define the logical Redis runtime layout for current turn, current cycle, current round guess state, and cleanup deadlines.
- [ ] Store runtime values with room-scoped keys and bounded lifetime.
- [ ] Keep runtime state recoverable enough that durable game rows remain the long-lived source of truth.
- [ ] Add explicit Redis-side support for the room-scoped round payload data used to differentiate drawer vs non-drawer round-start messages.
- [ ] Expose helper functions or a service interface that higher-level game logic can call.
- [ ] Add Redis/helper tests for turn, cycle, guess-state, cleanup-key, and round-payload behavior.
- [ ] Verification gate: runtime state operations are isolated per room and support the later turn and guess lifecycle work.

---

#### N-04: Drawing Event Broadcast & Canvas Snapshot Sync
**Source Ticket**: `T-12`  
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `N-02`, `N-03`  
**Impacts**: Core live drawing visibility and reconnect drawing recovery  
**Blocks**: `G-04`, `G-05`

**Deliverables**:
- Drawer-authorized `drawing.stroke`, `drawing.end_stroke`, and `drawing.clear` socket events
- Drawing broadcast to room participants
- Latest canvas snapshot persistence in Redis
- Reconnect snapshot replay behavior

- [ ] Accept `drawing.stroke`, `drawing.end_stroke`, and `drawing.clear` only from the active drawer.
- [ ] Broadcast those live drawing events to other room participants through the room socket.
- [ ] Persist the latest canvas snapshot in Redis for reconnect sync.
- [ ] Reset or refresh the stored canvas snapshot correctly when clear events occur.
- [ ] Replay the latest canvas snapshot to newly connected or reconnecting participants in the active round.
- [ ] Add consumer tests for drawer-only drawing authorization and snapshot replay behavior.
- [ ] Verification gate: reconnecting clients can recover the current drawing state without replaying historical room data from MySQL.

---

#### N-05: Guess Submission Pipeline Over Room Socket
**Source Ticket**: `T-15`  
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `N-02`, `K-03`, `K-01`  
**Impacts**: Live guess entry into the server-authoritative gameplay loop  
**Blocks**: `K-05`, `K-06`, `G-03`

**Deliverables**:
- `guess.submit` client-to-server event handling
- Round-bound guess persistence
- `guess.result` server-to-client event handling

- [ ] Accept live guess submissions through the room socket.
- [ ] Validate that the submitting participant belongs to the room and is eligible to guess in the current round.
- [ ] Persist guesses on the active round while the round is live.
- [ ] Broadcast normalized guess-result payloads back to room participants.
- [ ] Keep socket transport thin by handing rule evaluation back to the gameplay service layer.
- [ ] Add consumer and service tests for valid submissions, invalid submissions, and result broadcasts.
- [ ] Verification gate: live guess submission works over the room socket and results are server-evaluated, not client-derived.
