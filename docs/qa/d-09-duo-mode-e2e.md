# D-09 Duo Mode End-To-End Verification

Date: 2026-05-20
Branch: `nikos/d-09`
Base: latest `origin/main`

## Dependency State

`origin/main` includes the required D-track dependency merges:

- D-04: `5a3a274 Merge pull request #112 from alexsmyr0/nikos/d-04`
- D-05: `9243e58 Merge pull request #113 from alexsmyr0/zoghs/d-05`
- D-06: `d44a089 Merge pull request #115 from alexsmyr0/zoghs/d-06`
- D-07: `c78c7c7 Merge pull request #116 from alexsmyr0/nikos/d-07`
- D-08: `a9b7794 Merge pull request #114 from alexsmyr0/alexsmyro/D-08`

D-09 was therefore created directly from `origin/main`, not as a stacked branch.

## Environment

- Runtime: Docker Compose MySQL and Redis services.
- Test driver: Django Channels `WebsocketCommunicator` multi-client run using the production room socket routing and session middleware.
- Client count: 4 logical connected clients.
- Roles during the verified duo round:
  - Client A: host
  - Client B: participant
  - Client C: participant / guesser
  - Client D: participant / extra guesser

## Manual Multi-Client Run

This run was documented while driving a real room flow through four independent socket clients. The same scenario is now captured as the automated D-09 regression test:

`rooms.tests.test_consumers.RoomConsumerConnectTests.test_d09_duo_round_e2e_covers_mode_draw_pair_canvas_cochat_and_scoring`

Steps and results:

1. Connected four separate room clients with distinct Django sessions.
   - Expected: each receives an initial `room.state`.
   - Actual: pass.
2. Host changed `game_mode` to `duo` through the room settings HTTP endpoint.
   - Expected: connected clients receive a live `room.state` showing `game_mode: duo`.
   - Actual: pass.
3. Started the game from the duo lobby state.
   - Expected: first round selects two distinct drawers.
   - Actual: pass; `drawer_participant_id` and `second_drawer_participant_id` were both present and distinct.
4. Verified word visibility.
   - Expected: both drawers receive `round.drawer_word`; non-drawers do not.
   - Actual: pass.
5. Sent drawing strokes from both drawers over the room socket.
   - Expected: both strokes broadcast to viewers and persist into the shared room canvas snapshot.
   - Actual: pass; Redis canvas snapshot included both drawer strokes.
6. Sent a drawer co-chat message.
   - Expected: only the paired drawer receives `cochat.message`; guessers receive no co-chat payload.
   - Actual: pass.
7. Submitted a correct guess from a non-drawer.
   - Expected: guesser receives normal score; both drawers receive split bonus.
   - Actual: pass. With a patched 50% round-time point, guesser received `60`, each drawer received `15`, total drawer payout `30`.

## Additional Verified Coverage

- Co-chat negative coverage already exists for guessers, spectators, normal-mode rounds, and reconnect/no replay in `rooms.tests.test_consumers`.
- Disconnect behavior is covered by D-07 runtime and consumer tests:
  - single duo drawer disconnect keeps the round active without grace,
  - both drawers disconnected starts grace,
  - either drawer reconnect during grace resumes,
  - grace expiry ends the round as `drawer_disconnected`.
- Mode reset and normal-mode leak prevention are covered by room settings/view tests and D-08 JavaScript tests, including normal-mode co-chat visibility.

## Bugs Found

No blocking duo integration bugs were found during the D-09 run.

## Remaining Observations

- The project planning tracker still showed D-07 and D-08 unchecked even though their merge commits are present on `origin/main`.
- The local host Python test path cannot reach the Docker-only MySQL hostname `mysql`; Docker Compose is the reliable test execution path for this project.
