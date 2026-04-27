# SketchIt Progress Context

This file is a concise snapshot of the current implementation state. It describes what exists in the codebase today, not the intended final MVP behavior.

## Overall state

- The repository is a functional Django project with a multi-client real-time game loop.
- Core domain models exist for rooms, participants, games, rounds, guesses, and words.
- Ticket G-02 (Live Lobby Page Template & Room Client) has been implemented, providing a synchronized real-time lobby experience.

## UI

- Real-time product UI is implemented through `room_entry.html` and a unified `room_lobby.html` experience.
- `room_lobby.js` now covers both the synchronized lobby and the G-03 gameplay HUD: room-state sync, host controls, timer/score rendering, guess input/result handling, spectator lockout, reconnect-safe round sync, and intermission/final leaderboard overlays.
- The old standalone gameplay template/client files were removed so the room page remains the single browser surface for lobby plus gameplay state.
- Premium styling has been applied to the entry and room screens.

## Backend

- Django is configured with apps for `core`, `rooms`, `games`, and `words`.
- `rooms/views.py` includes fully functional views for creating rooms, joining rooms, lobby state, and updating settings.
- `rooms/services.py` centralizes participant lifecycle logic (connect, disconnect, leave, host handoff) and handles real-time broadcasts.
- `games/services.py` and `games/runtime.py` exist for gameplay logic.

## WebSockets / Real-time

- Channels is installed and ASGI is fully configured.
- `rooms/consumers.py` implements a robust `RoomConsumer` for real-time communication.
- Real-time events include `room.state`, `host.changed`, `drawing.stroke`, and `guess.submit`.
- WebSocket routing is wired in `config/routing.py`.

## Database

- Persistent models exist in `rooms`, `games`, and `words`.
- `Room`, `Player`, `Game`, `GameWord`, `Round`, `Guess`, `WordPack`, `Word`.
- MySQL is used for persistent storage, as specified in the PRD.

## Game logic

- The game service and runtime layers drive turn flow, scoring, round transitions, leaderboard cooldowns, and drawer-disconnect handling.
- The browser now renders the gameplay shell for guessing rounds and intermission states, while drawing-specific browser work remains separate under G-04.

## AI integration

- No AI features or integrations exist in the codebase currently.

## Infrastructure

- The project uses MySQL for persistent data and Redis for Channels and real-time state.
- Docker configuration is provided for all services.
- Cleanup management commands (e.g. for empty rooms) exist.

## Testing

- Automated tests exist for views, services, models, and real-time consumers.
- Tests use `fakeredis` for Redis isolation and standard Django test runner with MySQL.
