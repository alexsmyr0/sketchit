# Ticket Progress Tracker

This file tracks delivery progress for the final `A / K / N / G` track split.

Detailed ticket definitions live in:

- `docs/planning/track-a.md`
- `docs/planning/track-k.md`
- `docs/planning/track-n.md`
- `docs/planning/track-g.md`

The original coverage and source-ticket mapping remain canonical in:

- `docs/planning/tickets-by-topic.md`
- `docs/planning/prd.md`
- `docs/planning/sds.md`

## Update Rules

1. Keep each ticket in the line format: status + ticket ID + short description + dependency fields.
2. Use `[x]` only when the verification gate in the owning track file is satisfied.
3. Use `[-]` only when a meaningful subset of that ticket already exists in code.
4. Keep `Depends on` and `Blocks` synchronized with the owning track file when ticket definitions change.
5. Do not remove completed tickets from the tracker.

## Status Legend

- `[ ]` = Not Started
- `[-]` = Partially Implemented / In Progress
- `[x]` = Done

## Execution Policy (Low-Blocking First)

1. Respect the canonical phase order: `P0 -> P1 -> P2 -> P3 -> P4`.
2. Inside each phase, prioritize tickets that unblock the most other tracks.
3. Track `G` owns all browser UI, client integration, and QA/hardening only.
4. Track `N` owns Redis, Channels, room socket transport, and realtime runtime state.
5. Tracks `A` and `K` split backend ownership, with `A` leaning persistence / room lifecycle and `K` leaning gameplay rule backend.
6. When two tickets are both available, prefer the one that reduces cross-track waiting first and personal-preference alignment second.

## Summary Snapshot

- Total tickets: `26`
- Done: `7`
- Partially Implemented: `3`
- Not Started: `16`

## Low-Blocking Claim Queue (Global)

Use this as the default claim order for the next wave of work:

1. **Q0 P1 Backend Unblockers**: `N-02`, `A-03`, `A-04`, `K-02`
2. **Q1 P1 Lobby Completion**: `A-06`, `G-01`, `G-02`, `A-05`
3. **Q2 P2 Runtime Spine**: `N-03`, `K-03`, `K-04`, `N-04`, `N-05`
4. **Q3 P3 Full Rules**: `K-05`, `K-06`, `A-07`, `K-07`, `A-08`
5. **Q4 Browser Gameplay + QA**: `G-03`, `G-04`, `G-05`, `G-06`

## Ticket ID Index

- Track A: `A-01` through `A-08`
- Track K: `K-01` through `K-07`
- Track N: `N-01` through `N-05`
- Track G: `G-01` through `G-06`

## Ordered Tickets By Track

### Track A

- [x] **A-01** P0 - Persistent Domain Models & Word Setup Baseline | Core Django/MySQL models, migrations, and default word-pack setup. (Depends on: None) | Blocks: A-02; N-01; K-01; N-04; K-04
- [x] **A-02** P0 - Room Entry HTTP Baseline | Guest room create/join, session reuse, lobby read, and host start-game HTTP. (Depends on: A-01) | Blocks: K-02; A-03; G-01; G-02
- [x] **A-03** P1 - Lobby Settings Update API | Host-only lobby edits for room name and visibility. (Depends on: A-02) | Blocks: G-02
- [ ] **A-04** P1 - Host Reassignment & Participant Connection Lifecycle | Host handoff and participant connect/disconnect/leave state transitions. (Depends on: A-02) | Blocks: A-05; A-06; A-07; A-08
- [ ] **A-05** P1 - Empty-Room Grace Lifecycle | `10`-minute empty-room grace, resurrection, and hard-delete cleanup. (Depends on: N-01; A-04) | Blocks: A-08; G-06
- [ ] **A-06** P1 - Live Lobby State Broadcast | Authoritative `room.state` events and initial live lobby snapshots. (Depends on: A-04; N-02) | Blocks: G-02; G-03
- [ ] **A-07** P3 - Mid-Game Joiners, Spectators, And Reconnect Reclaim | Spectator rules, next-turn eligibility, and reconnect score preservation. (Depends on: A-04; N-03; K-04) | Blocks: A-08; G-05
- [-] **A-08** P3 - Game Finish, Leaderboard Cooldown, And Auto-Restart | End-of-game loop, leaderboard cooldown, and next-game restart policy. (Depends on: K-04; K-05; A-07; K-07) | Blocks: G-05; G-06

### Track K

- [x] **K-01** P0 - Game Bootstrap & Basic Guess Service Baseline | Game creation, room word snapshot, first round, and placeholder guess resolution. (Depends on: A-01; A-02) | Blocks: N-03; K-03; K-04; K-05
- [x] **K-02** P1 - Public Room Directory API | Public-room discovery endpoint including in-progress public rooms. (Depends on: A-02) | Blocks: G-01
- [x] **K-03** P2 - Round Timer & Early-Finish Coordinator | Server-owned round timing, intermission countdown, and early-finish triggers. (Depends on: K-01; N-03) | Blocks: K-04; N-05; K-05; K-07; G-03
- [-] **K-04** P2 - Full Game Cycle Drawer Rotation & Word Uniqueness | Remaining-drawer tracking, unique words, and role-specific round-start payloads. (Depends on: K-01; N-03; K-03) | Blocks: A-07; K-07; A-08
- [ ] **K-05** P3 - Time-Based Scoring & Multi-Guesser Round Resolution | Replace placeholder scoring with the final bounded time-based model. (Depends on: K-03; K-04; N-05) | Blocks: A-08; G-05
- [ ] **K-06** P3 - Near-Match, Duplicate, And Correct-Once Guess Rules | Rich guess outcomes and per-player round guess-state rules. (Depends on: N-03; N-05) | Blocks: G-05
- [ ] **K-07** P3 - Drawer Disconnect Grace & Turn Outcome Handling | `15`-second drawer grace, reconnect resume, and `drawer_disconnected` outcome. (Depends on: N-03; K-03; K-04) | Blocks: A-08; G-05

### Track N

- [x] **N-01** P0 - Redis Room Runtime Helper Baseline | Presence keys, canvas snapshot keys, and transient Redis TTL helpers. (Depends on: A-01) | Blocks: N-02; N-03; N-04; N-05
- [x] **N-02** P1 - Room WebSocket Consumer & Session Authorization | Room socket routing, session membership checks, and group join/leave behavior. (Depends on: A-02; N-01) | Blocks: N-03; N-04; N-05; G-02; G-03; G-04
- [x] **N-03** P2 - Redis Round Runtime State | Active round runtime state for timers, drawer pools, guess state, cleanup keys, and role-specific round payload support. (Depends on: N-01; K-01; N-02) | Blocks: N-04; K-03; K-04; K-05; A-07; K-07; A-08
- [x] **N-04** P2 - Drawing Event Broadcast & Canvas Snapshot Sync | Drawer-only drawing events, canvas snapshot storage, and reconnect replay. (Depends on: N-02; N-03) | Blocks: G-04; G-05
- [ ] **N-05** P2 - Guess Submission Pipeline Over Room Socket | Live `guess.submit` handling and server-broadcast `guess.result` events. (Depends on: N-02; K-03; K-01) | Blocks: K-05; K-06; G-03

### Track G

- [x] **G-01** P1 - Entry Screen & Public Room Discovery UI | Browser entry page, create/join forms, and public-room list rendering. (Depends on: A-02; K-02) | Blocks: G-02
- [ ] **G-02** P1 - Live Lobby Page Template & Room Client | Lobby page, room socket client, derived join URL, and host controls. (Depends on: A-03; N-02; A-06; G-01) | Blocks: G-03; G-04
- [ ] **G-03** P2 - Gameplay Page Shell, HUD, And Guess UI | Gameplay browser shell, timer/score HUD, and guess input/result rendering. (Depends on: A-06; K-03; K-04; N-05; G-02) | Blocks: G-05
- [ ] **G-04** P2 - Browser Drawing Surface & Room Socket Integration | Drawer canvas UI, viewer drawing replay, and clear/end-stroke browser handling. (Depends on: N-04; G-02) | Blocks: G-05
- [ ] **G-05** P4 - End-To-End Multiplayer Verification & Bug Sweep | Full MVP verification, multi-client testing, and bug-fix sweep. (Depends on: N-04; K-05; K-06; K-07; A-08; G-03; G-04) | Blocks: G-06
- [ ] **G-06** P4 - Shared Context Refresh & Final MVP Hardening | Final docs refresh, cleanup fixes, and planning consistency pass. (Depends on: A-05; A-08; G-05) | Blocks: None

## Cross-Document References

- Source backlog and audit status: `docs/planning/tickets-by-topic.md`
- Track A definitions: `docs/planning/track-a.md`
- Track K definitions: `docs/planning/track-k.md`
- Track N definitions: `docs/planning/track-n.md`
- Track G definitions: `docs/planning/track-g.md`
