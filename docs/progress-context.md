# SketchIt Progress Context

This file is a concise snapshot of the current implementation state. It describes what exists in the codebase today, not the intended final MVP behavior.

## Overall state

- The repository is an early Django project scaffold.
- Core domain models exist for rooms, participants, games, rounds, guesses, and words.
- The app is not yet playable end to end.
- `Example_starting_code/` is reference material only and is not part of the live app.

## UI

- No live product UI is implemented in the main app.
- No templates, frontend app, or browser game screens exist outside `Example_starting_code/`.
- Django admin is the only wired HTTP route.

## Backend

- Django is configured with apps for `core`, `rooms`, `games`, and `words`.
- App views are still stubs.
- No REST API, form flow, or room/game endpoints are implemented.

## WebSockets / Real-time

- Channels is installed and ASGI is configured.
- Project websocket routing is currently empty.
- No consumers or realtime event flows exist in the live app.

## Database

- Persistent models exist in `rooms`, `games`, and `words`.
- `rooms` currently defines `Room` and `Player`.
- `games` currently defines `Game`, `GameWord`, `Round`, and `Guess`.
- `words` currently defines `WordPack`, `Word`, and `WordPackEntry`.
- A default seeded word pack exists through migration.
- Some intended product relationships are not implemented yet, such as a room-to-word-pack relation.

## Game logic

- There is no implemented game service layer yet.
- Some model-level validation exists for game and round consistency.
- Turn flow, scoring rules, guess evaluation, reconnect handling, host reassignment, timers, and cleanup behavior are not implemented yet.

## AI integration

- No AI features or integrations exist in the codebase.

## Infrastructure

- Settings support MySQL with SQLite fallback.
- Settings support Redis channel layers with in-memory fallback.
- Local Docker files exist for app, MySQL, and Redis setup.

## Testing

- No automated tests are implemented yet.
- `manage.py test` currently runs zero tests.

## Notes for future work

- Use this file as implementation-state context.
- Use `docs/mvp-spec.md` as intended-product context.
