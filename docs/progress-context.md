# SketchIt Progress Context

This file is a concise snapshot of the current implementation state. It describes what exists in the codebase today, not the intended final MVP behavior.

## Overall state

- The repository is a functional Django project with a multi-client real-time game loop.
- Core domain models exist for rooms, participants, games, rounds, guesses, and words.
- Ticket G-02 (Live Lobby Page Template & Room Client) has been implemented, providing a synchronized real-time lobby experience.

## UI

- Real-time product UI is partially implemented.
- `room_entry.html` (G-01) and `room_lobby.html` (G-02) exist and are fully functional.
- `room_lobby.js` handles WebSocket synchronization, host controls (starting game, updating settings), and participant list updates.
- Premium styling has been applied to the entry and lobby screens.

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

- A game service layer is being implemented.
- Turn flow, scoring, and round transitions are partially implemented.
- Drawers can draw, and non-drawers can submit guesses (G-03/G-04 implementation in progress or partially present).

## AI integration

- No AI features or integrations exist in the codebase currently.

## Infrastructure

- The project uses MySQL for persistent data and Redis for Channels and real-time state.
- Docker configuration is provided for all services.
- Cleanup management commands (e.g. for empty rooms) exist.

## Testing

- Automated tests exist for views, services, models, and real-time consumers.
- Tests use `fakeredis` for Redis isolation and standard Django test runner with MySQL.
