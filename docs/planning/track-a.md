# Track A — Persistence, Room Lifecycle & Session-State Backend

Source backlog: `docs/planning/tickets-by-topic.md`

> Scope: Django/MySQL-backed room and participant lifecycle, lobby configuration, host ownership, live lobby room-state broadcasting, empty-room cleanup, spectator/reconnect rules, and room-owned game completion policy. This track should avoid websocket transport ownership and avoid browser UI ownership.

## Phase Order

- **P0 Existing Baseline**: `A-01` to `A-02`
- **P1 Realtime Lobby**: `A-03` to `A-06`
- **P2 Playable Round Loop**: No new Track A tickets
- **P3 Full Game Rules**: `A-07` to `A-08`
- **P4 Hardening & QA**: No new Track A tickets

---

#### A-01: Persistent Domain Models & Word Setup Baseline
**Source Ticket**: `T-01`  
**Priority**: Critical  
**Phase**: P0 Existing Baseline  
**Depends On**: None  
**Impacts**: Durable schema ownership for rooms, participants, games, rounds, guesses, and word lists  
**Blocks**: `A-02`, `N-01`, `K-01`, `N-04`, `K-04`

**Deliverables**:
- Persistent Django models for rooms, participants, games, rounds, guesses, words, and word packs
- MySQL migrations for the MVP baseline schema
- Room-to-word-pack association and default word-pack wiring
- Seeded default word pack for local development and tests

- [ ] Confirm integer-primary-key Django models for `rooms`, `games`, and `words`.
- [ ] Ensure required timestamps exist across all persistent domain models.
- [ ] Keep room, participant, game, round, and word relationships aligned with the PRD and SDS.
- [ ] Seed a default word pack that allows room creation without manual setup.
- [ ] Cover model constraints, admin registration, and seed behavior with tests.
- [ ] Verification gate: migrations apply cleanly and model/seed tests prove the baseline schema is valid for room creation and game bootstrap.

---

#### A-02: Room Entry HTTP Baseline
**Source Ticket**: `T-02`  
**Priority**: Critical  
**Phase**: P0 Existing Baseline  
**Depends On**: `A-01`  
**Impacts**: Guest entry path into rooms, initial host creation, and baseline room authorization flow  
**Blocks**: `K-02`, `A-03`, `G-01`, `G-02`

**Deliverables**:
- HTTP endpoints for room creation and joining
- Session-backed participant creation and same-session rejoin behavior
- Room-capacity enforcement for a maximum of `6` participants
- Lobby-state and host-only start-game HTTP baseline

- [ ] Provide room creation for guest users with `name`, `visibility`, and `display_name`.
- [ ] Provide room joining by `join_code` with Django-session participant ownership.
- [ ] Enforce the `6`-participant maximum for new room joins while still allowing same-session rejoin reuse.
- [ ] Reuse the same participant slot for same-session rejoin instead of duplicating room membership.
- [ ] Expose the room lobby-state read endpoint and the host-only start-game endpoint.
- [ ] Add HTTP tests for create, join, lobby access, and start-game authorization.
- [ ] Verification gate: guest create/join/start flows succeed or fail with correct status codes and no partial data corruption.

---

#### A-03: Lobby Settings Update API
**Source Ticket**: `T-06`  
**Priority**: Medium  
**Phase**: P1 Realtime Lobby  
**Depends On**: `A-02`  
**Impacts**: Host-controlled lobby setup before the room enters live play  
**Blocks**: `G-02`

**Deliverables**:
- Host-only room settings update endpoint or action
- Validation for `name` and `visibility`
- Lobby-only edit enforcement

- [ ] Allow only the current host to update room `name` and `visibility`.
- [ ] Reject room setting edits when the room is not in `lobby`.
- [ ] Keep word-pack selection out of the MVP room-settings surface.
- [ ] Return an updated room-state payload suitable for live lobby refresh.
- [ ] Add tests for host authorization, payload validation, and lobby-only restrictions.
- [ ] Verification gate: room settings can only be changed by the host while the room is still in `lobby`.

---

#### A-04: Host Reassignment & Participant Connection Lifecycle
**Source Ticket**: `T-07`  
**Priority**: Critical  
**Phase**: P1 Realtime Lobby  
**Depends On**: `A-02`  
**Impacts**: Stable host ownership and participant state management across leaves and reconnects  
**Blocks**: `A-05`, `A-06`, `A-07`, `A-08`

**Deliverables**:
- Service logic for participant connect/disconnect transitions
- Host reassignment logic when the current host leaves
- Persistent participant state updates for live room transitions

- [ ] Define how participant connection state changes on connect, disconnect, leave, and reconnect.
- [ ] Reassign the host randomly when the current host leaves and other participants remain.
- [ ] Clear host ownership when no participants remain in the room.
- [ ] Keep participant state transitions compatible with session-based room ownership.
- [ ] Update persistent participant rows without introducing duplicate room membership.
- [ ] Add tests for host handoff and participant lifecycle transitions.
- [ ] Verification gate: host reassignment and participant state transitions leave the room in a valid state every time.

---

#### A-05: Empty-Room Grace Lifecycle
**Source Ticket**: `T-08`  
**Priority**: High  
**Phase**: P1 Realtime Lobby  
**Depends On**: `N-01`, `A-04`  
**Impacts**: Room cleanup, room resurrection during grace, and long-lived room hygiene  
**Blocks**: `A-08`, `G-06`

**Deliverables**:
- Empty-room grace logic for `10`-minute retention
- Durable `empty_since` handling plus cleanup timing
- Hard-delete flow when grace expires

- [ ] Move a room into `empty_grace` when it becomes empty.
- [ ] Persist the empty-room timestamp so cleanup survives process restarts.
- [ ] Clear empty-room state if someone rejoins before the deadline.
- [ ] Hard-delete the room when the grace deadline expires with no rejoin.
- [ ] Add tests for enter-grace, rejoin-before-expiry, and delete-after-expiry behavior.
- [ ] Verification gate: empty rooms clean up correctly without deleting rooms that were rejoined in time.

---

#### A-06: Live Lobby State Broadcast
**Source Ticket**: `T-10`  
**Priority**: Critical  
**Phase**: P1 Realtime Lobby  
**Depends On**: `A-04`, `N-02`  
**Impacts**: Live room-state rendering for the lobby and later gameplay shell  
**Blocks**: `G-02`, `G-03`

**Deliverables**:
- `room.state` server event payload
- Host-change and participant-status live broadcast behavior
- Initial room-state snapshot for newly connected clients

- [ ] Broadcast room-state updates when participants join, leave, disconnect, reconnect, or when host ownership changes.
- [ ] Include enough room metadata for live lobby rendering.
- [ ] Send an initial room-state snapshot on successful socket connect.
- [ ] Keep room-state payloads server-authoritative so the client only renders.
- [ ] Add consumer-facing tests for room-state and host-change broadcasts.
- [ ] Verification gate: two connected participants see the same authoritative lobby state updates in realtime.

---

#### A-07: Mid-Game Joiners, Spectators, And Reconnect Reclaim
**Source Ticket**: `T-18`  
**Priority**: Critical  
**Phase**: P3 Full Game Rules  
**Depends On**: `A-04`, `N-03`, `K-04`  
**Impacts**: Correct participant eligibility rules during active games and reconnect continuity  
**Blocks**: `A-08`, `G-05`

**Deliverables**:
- Mid-game join spectator handling
- Next-turn eligibility inclusion for mid-game joiners
- Reconnect reclaim flow for non-drawer participants

- [ ] Mark mid-game joiners as spectators for the current turn.
- [ ] Prevent current-turn guessing by participants who joined after the round started.
- [ ] Add valid mid-game joiners into the remaining eligible drawer pool on the next turn.
- [ ] Restore the same participant row and score when a non-drawer reconnects during the same game.
- [ ] Keep spectator transitions compatible with room lifecycle and session ownership rules.
- [ ] Add service and consumer-facing tests for spectator restrictions and reconnect reclaim behavior.
- [ ] Verification gate: joining or reconnecting mid-game preserves fairness and does not create duplicate participants or score loss.

---

#### A-08: Game Finish, Leaderboard Cooldown, And Auto-Restart
**Source Ticket**: `T-20`  
**Priority**: Critical  
**Phase**: P3 Full Game Rules  
**Depends On**: `K-04`, `K-05`, `A-07`, `K-07`  
**Impacts**: Complete room-owned game loop and replayability inside one room  
**Blocks**: `G-05`, `G-06`

**Deliverables**:
- Game completion detection
- `20`-second leaderboard state
- Automatic next-game start when players remain
- Score reset between games

- [ ] Finish a game when the remaining eligible drawer pool is exhausted.
- [ ] Cancel a game if all players leave during active play.
- [ ] Publish the authoritative leaderboard window for `20` seconds after game finish.
- [ ] Automatically start a fresh game with reset scores if players remain after the cooldown.
- [ ] Keep room/game state transitions compatible with room cleanup and reconnect rules.
- [ ] Add service and integration tests for finish, cancel, leaderboard, and auto-restart behavior.
- [ ] Verification gate: one room can move from lobby through a completed game and into the next game automatically.
