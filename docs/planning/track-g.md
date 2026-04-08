# Track G — Browser UI, Client Sync & QA

Source backlog: `docs/planning/tickets-by-topic.md`

> Scope: All frontend/UI implementation, browser-side room and gameplay rendering, drawing UI, and final QA/hardening. This track should not own server-side transport or backend rule logic.

## Phase Order

- **P0 Existing Baseline**: No new Track G tickets
- **P1 Realtime Lobby**: `G-01` to `G-02`
- **P2 Playable Round Loop**: `G-03` to `G-04`
- **P3 Full Game Rules**: No new Track G tickets
- **P4 Hardening & QA**: `G-05` to `G-06`

---

#### G-01: Entry Screen & Public Room Discovery UI
**Source Ticket**: `T-21`  
**Priority**: Medium  
**Phase**: P1 Realtime Lobby  
**Depends On**: `A-02`, `K-02`  
**Impacts**: User-facing room entry and public-room discovery  
**Blocks**: `G-02`

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

#### G-02: Live Lobby Page Template & Room Client
**Source Ticket**: `T-22`  
**Priority**: Critical  
**Phase**: P1 Realtime Lobby  
**Depends On**: `A-03`, `N-02`, `A-06`, `G-01`  
**Impacts**: Playable live lobby and host-controlled start flow  
**Blocks**: `G-03`, `G-04`

**Deliverables**:
- Room page template for the lobby state
- Vanilla JS room client for socket connect and room-state rendering
- Derived join URL display and copy control
- Host start-game and lobby-settings controls

- [ ] Render room metadata, host indicator, participant list, and room status on the room page.
- [ ] Connect the page to the room socket and update the lobby UI from server-sent room-state events.
- [ ] Show the derived join URL on the room page and provide a simple copy action for sharing room links.
- [ ] Show host-only controls for start-game and lobby settings updates.
- [ ] Show non-host participants a correct read-only view.
- [ ] Add frontend and HTTP tests for room-page access and key lobby rendering behavior where practical.
- [ ] Verification gate: multiple browser sessions can stay in sync inside the live lobby and the host can start the game from the page.

---

#### G-03: Gameplay Page Shell, HUD, And Guess UI
**Source Ticket**: `T-23`  
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `A-06`, `K-03`, `K-04`, `N-05`, `G-02`  
**Impacts**: Playable non-drawer gameplay view and server-driven timer and score visibility  
**Blocks**: `G-05`

**Deliverables**:
- Gameplay room page state on the existing room page or a game-mode section within it
- HUD for timer, participants, scores, and round state
- Guess input and guess-result rendering
- Leaderboard and between-turn UI states

- [ ] Render round timer, participant scores, and current game state from server-owned events.
- [ ] Provide guess input for eligible non-drawer participants.
- [ ] Render masked or partial word information appropriately for non-drawers.
- [ ] Render server-provided guess-result states generically so later backend additions can plug into the same UI surface without redesign.
- [ ] Show leaderboard and between-turn states based on server events rather than local guesses.
- [ ] Keep drawer and non-drawer page behavior role-aware.
- [ ] Add frontend tests for guess-input state and timer/scoreboard rendering where practical.
- [ ] Verification gate: a non-drawer can watch the timer, submit guesses, see results, and follow round-to-round progression from the browser.

---

#### G-04: Browser Drawing Surface & Room Socket Integration
**Source Ticket**: `T-24`  
**Priority**: Critical  
**Phase**: P2 Playable Round Loop  
**Depends On**: `N-04`, `G-02`  
**Impacts**: Playable live drawing experience for the active drawer and synchronized viewing for everyone else  
**Blocks**: `G-05`

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

---

#### G-05: End-To-End Multiplayer Verification & Bug Sweep
**Source Ticket**: `T-25`  
**Priority**: Critical  
**Phase**: P4 Hardening & QA  
**Depends On**: `N-04`, `K-05`, `K-06`, `K-07`, `A-08`, `G-03`, `G-04`  
**Impacts**: Confidence that the MVP works as one system instead of isolated features  
**Blocks**: `G-06`

**Deliverables**:
- Integration and consumer test coverage for the core multiplayer loop
- Bug-fix pass driven by real cross-browser or multi-client verification
- MVP verification checklist derived from the PRD and SDS

- [ ] Add or complete multi-client integration tests that cover room entry, lobby sync, gameplay, scoring, and next-game progression.
- [ ] Add consumer tests for the core room-socket event families.
- [ ] Run a bug-fix pass against issues found during full MVP verification.
- [ ] Verify drawer/non-drawer role behavior, reconnect handling, and room cleanup flows.
- [ ] Record any rule changes discovered during QA back into the planning docs before finalizing implementation closure.
- [ ] Verification gate: the team can run a reproducible end-to-end MVP verification pass without relying on ad hoc manual reasoning.

---

#### G-06: Shared Context Refresh & Final MVP Hardening
**Source Ticket**: `T-26`  
**Priority**: Medium  
**Phase**: P4 Hardening & QA  
**Depends On**: `A-05`, `A-08`, `G-05`  
**Impacts**: Final planning/documentation consistency and reduced confusion for the team after implementation  
**Blocks**: None

**Deliverables**:
- Refreshed implementation-state documentation
- Final hardening fixes for cleanup, configuration, or edge-case regressions discovered late
- Final planning-ready state for implementation tracking

- [ ] Refresh progress and implementation-state documentation after the full MVP verification pass.
- [ ] Fold in final cleanup fixes for room deletion, restart behavior, and client-edge regressions discovered during QA.
- [ ] Confirm tracker state, track files, and planning docs remain synchronized.
- [ ] Leave a final handoff-ready state that future issue audits can consume without re-deriving scope.
- [ ] Verification gate: the planning set reflects the real implemented MVP and no final hardening item remains undocumented.
