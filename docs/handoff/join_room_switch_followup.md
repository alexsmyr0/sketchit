# Handoff: join_room switch works in tests, fails in browser

Branch: `alexsmyro/Codebase-audit` (tip `686fbdf` at handoff time).

## What we already did

Commit `24908e7` — `fix(rooms): join_room switches to explicit target instead of returning 409`. Two changes:

1. **`rooms/views.py` `join_room`** — when the session already has a `Player` in a *different* room than the URL target, call `leave_participant(redis_client=..., player_id=existing_player.id)` and fall through to the normal new-Player creation path. Same-room rejoin still returns 200 with no churn.
2. **`rooms/tests/test_views.py`** — replaced `test_join_room_returns_recoverable_conflict_for_valid_other_room_assignment` with `test_join_room_switches_session_to_target_when_already_in_other_room`. The new test creates Player A in `other_room`, posts a join to `self.room`, asserts response is 201 with target room's join_code, asserts exactly one `Player` remains and it's in the target room, asserts vacated room transitioned to `EMPTY_GRACE` with no host.

**Test passes.** Full suite is green 8/8.

`create_room` was intentionally **not** changed — it still returns 409 with a recovery URL because there's no explicit target on create. Only `join_room` switches.

## The bug the user is seeing

> "Joining or creating another room while already in one ... I still join the old room."

User did `docker compose up --build` and tested in browser. Despite the unit test passing, the live UX still routes them back to the old room.

## Suspected causes (most → least likely)

1. **The frontend isn't hitting `/rooms/<code>/join/`** at all on the Play button. The entry page may read `?code=<join_code>` from the URL, then route to `/rooms/<code>/` (lobby state, a GET) rather than POST `/rooms/<code>/join/`. The lobby-state view does not call `leave_participant`. Check `rooms/templates/rooms/room_entry.html`, `rooms/static/rooms/room_entry.js`, and whatever handles the Play submit. Trace the actual request: open DevTools → Network, click Play, confirm method + URL + status.
2. **The frontend issues `POST /rooms/create/`** instead of `/join/` for "I have a code, take me there." `create_room` still has the strict 409 behaviour from G.4d (`c39810f`) and the entry page would honour the 409's `room_url` and navigate back to the old room. If this is happening, the fix is either in the frontend (call `/join/` when a code is supplied) or extend the switch behaviour to `create_room` when an existing-room-redirect URL is in scope.
3. **Browser session inconsistency.** If the user's session cookie expired between the OLD room creation and the new join, `purge_expired_participants_for_session` could have removed the old Player row, so the join path takes the "no existing player" branch and joins the target normally — but the OLD room still has the user as host (in DB) because the participant was purged with `participation_status` handling not host reassignment. Lower likelihood but worth eliminating.
4. **The user is in the same room and same-room rejoin path is firing** (returns 200, no switch). Would happen if they typed the same code as their current room. Verify the URL they typed differs from their current room's join code.
5. **Docker volume / build cache.** `docker compose up --build` should rebuild the app image, but if `--build` was skipped or a cached layer didn't bust, the running app might not include `24908e7`. Confirm by `docker compose exec app git log --oneline -5` from inside the container.

## How to debug quickly

```bash
# Confirm the running container has the fix
docker compose exec app git log --oneline -3

# Watch logs while reproducing
docker compose logs -f app

# In the browser DevTools Network tab, click Play and capture:
#   - request method (POST/GET)
#   - request URL
#   - request body
#   - response status + body
# This tells you exactly which view the click hits.
```

## Key files for the next session

- `rooms/views.py:join_room` (around the `if player.room_id != room.id:` block) — production code that calls `leave_participant`
- `rooms/views.py:create_room` (around the `existing_player is not None` block) — still 409, no switch
- `rooms/views.py:room_lobby_state` — pure GET, no membership mutation
- `rooms/templates/rooms/room_entry.html` — entry-page template
- `rooms/static/rooms/room_entry.js` — entry-page JS (likely owner of the Play button handler)
- `rooms/tests/test_views.py::JoinRoomViewTests::test_join_room_switches_session_to_target_when_already_in_other_room` — passing unit test that proves the backend works
