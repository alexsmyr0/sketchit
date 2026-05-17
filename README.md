# SketchIt

SketchIt is a real-time multiplayer drawing-and-guessing web game. One player draws a chosen word while the others race to guess it before the timer runs out. The server is authoritative for rooms, rounds, timing, scoring, and guess evaluation, so all players see a consistent game state.

## Features

- Create public or private rooms with shareable join codes
- Live drawing canvas synchronized over WebSockets
- Server-driven 90-second rounds with rotating drawers
- Time-weighted scoring for guessers and drawer bonuses
- Lobby with host controls, word-pack selection, and live participant list
- Reconnect support that preserves identity and score within a game
- Automatic host reassignment and empty-room cleanup

## Tech Stack

- **Backend:** Django 6, Django Channels (ASGI via Daphne)
- **Database:** MySQL 8
- **Runtime state / broker:** Redis 7
- **Frontend:** Django templates + vanilla JavaScript
- **Container runtime:** Docker

## Prerequisites

- Docker Desktop installed and running
- Docker Compose available through `docker compose`
- Free local ports: `8000` (app), `3306` (MySQL), `6379` (Redis)

## Quick Start (Docker, supported path)

From the project root:

```bash
docker compose up --build
```

What happens on startup:

1. Docker builds the Django image.
2. MySQL and Redis start and pass their health checks.
3. The `app` container waits for both backing services to be healthy.
4. Django runs `python manage.py migrate` automatically.
5. Daphne serves the ASGI app at <http://127.0.0.1:8000>.

Run the stack detached:

```bash
docker compose up --build -d
```

Stop the stack (keeps data):

```bash
docker compose down
```

Stop the stack and wipe MySQL + Redis volumes:

```bash
docker compose down -v
```

> Use `down -v` only when you want to delete local MySQL and Redis data.

## Useful Docker Commands

View running services:

```bash
docker compose ps
```

Tail logs from all services:

```bash
docker compose logs -f
```

Tail logs from only the app:

```bash
docker compose logs -f app
```

Open a shell inside the app container:

```bash
docker compose exec app sh
```

Open a Django shell:

```bash
docker compose exec app python manage.py shell
```

Apply migrations manually:

```bash
docker compose exec app python manage.py migrate
```

Generate new migrations after model changes:

```bash
docker compose exec app python manage.py makemigrations
docker compose exec app python manage.py migrate
```

Create a Django superuser for `/admin/`:

```bash
docker compose exec app python manage.py createsuperuser
```

Collect static files (when needed):

```bash
docker compose exec app python manage.py collectstatic --noinput
```

Rebuild just the app image after dependency changes:

```bash
docker compose build app
```

## Running Tests

Bring up the backing services first:

```bash
docker compose up -d mysql redis
```

Run the full test suite with the Docker-specific test settings:

```bash
docker compose run --rm app python manage.py test --settings=config.test_settings
```

Run a focused subset:

```bash
docker compose run --rm app python manage.py test rooms.tests games.tests --settings=config.test_settings
```

Notes:

- `config.test_settings` is the only supported Django test settings module.
- It is intended to be used from Docker, not from the host shell.
- It forces `MYSQL_HOST=mysql` and Docker test credentials.
- It swaps Channels to an in-memory layer so tests that do not need Redis transport can run without Redis under test.

## Environment Configuration

Docker Compose injects the required environment variables for local development. They are also documented in `.env.example` for non-Docker setups:

| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Django secret key |
| `DJANGO_DEBUG` | `True` for local development |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated trusted CSRF origins |
| `MYSQL_DATABASE` | Database name |
| `MYSQL_USER` | Database user |
| `MYSQL_PASSWORD` | Database password |
| `MYSQL_HOST` | Database host (`mysql` inside Compose) |
| `MYSQL_PORT` | Database port (default `3306`) |
| `REDIS_URL` | Redis connection URL (e.g. `redis://redis:6379/1`) |

The Django runtime requires MySQL and Redis. SQLite is not part of the active project setup.

## Manual (Non-Docker) Setup

Docker Compose is the supported path. If you want to run the app directly on the host, you must provide your own MySQL 8 and Redis 7 instances.

```bash
# 1. Create and activate a virtualenv
python3.12 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and edit environment variables
cp .env.example .env
# edit .env so MYSQL_HOST and REDIS_URL point at your local services

# 4. Export the variables (or use direnv / a dotenv loader)
export $(grep -v '^#' .env | xargs)

# 5. Run migrations
python manage.py migrate

# 6. Start the ASGI server
daphne -b 0.0.0.0 -p 8000 config.asgi:application
```

## Entry Points

- App: <http://127.0.0.1:8000>
- Admin: <http://127.0.0.1:8000/admin/>

## User Manual

### Joining a game

1. Open <http://127.0.0.1:8000>.
2. Pick a display name.
3. Either:
   - Enter a join code shared by a host and click **Join**, or
   - Click **Create Room** to host a new room, or
   - Browse the **Public rooms** directory to join an open room.

### Hosting a room

1. After creating a room you become the host.
2. In the lobby you can:
   - Toggle visibility between **Public** and **Private**.
   - Choose the **word pack** the round words come from.
   - Adjust the **max players** for the room.
3. Share the join code (or the public listing) with other players.
4. Once at least **2 participants** are connected and marked **playing**, click **Start game**.

If the host leaves the room mid-session, the server randomly promotes another participant to host and broadcasts the change to everyone.

### Playing a round

Each round lasts **90 seconds**.

- One participant is the **drawer**. They see the full target word and draw it on the canvas.
- Everyone else is a **guesser**. They see a masked hint and type guesses into the chat box.
- The server evaluates guesses live and replies with one of: `correct`, `near_match`, `duplicate`, or `incorrect`.
- The same player cannot score twice on the same round, but multiple guessers can each score on the same round while time remains.

A round ends when any of these is true:

1. The 90-second timer expires.
2. All eligible non-drawer guessers are already correct.
3. The drawer disconnects and does not return within a **15-second** grace window.

### Scoring

Let `remaining_ratio = remaining_ms / 90000`, clamped to `[0, 1]`.

```text
guesser_points = round(20 + remaining_ratio * 80)
drawer_bonus   = round(10 + remaining_ratio * 40)
```

- A guesser earns `guesser_points` at most once per round.
- The drawer earns `drawer_bonus` once for **each** distinct guesser who gets it right.
- Scores reset to `0` at the start of every new game.

### Reconnecting

Your identity is tied to your browser session. If you drop out and rejoin the same room during the same game:

- You reclaim your participant slot and current score.
- You receive the current room/game state and the latest canvas snapshot.
- If you were the drawer and reconnect within 15 seconds, the round continues.

### Ending a game and starting the next

When the eligible drawer pool runs out the game ends, a final leaderboard is shown, and a new game can be started by the host if at least 2 players are still connected.

### Leaving a room

Use the **Leave room** action from the lobby or game screen. If you were the host, another player is promoted automatically. If the room becomes empty it enters a 10-minute grace period and is then hard-deleted unless someone rejoins.

## Project Layout

```
config/   Django project settings, URLs, ASGI entry point
rooms/    Room lifecycle, lobby, presence, host assignment
games/    Game and round lifecycle, scoring, guess evaluation
words/    Word packs, word lists, per-game word snapshots
core/     Shared utilities
docs/     Planning docs, including the full SDS
```

## Further Documentation

- [docs/planning/sds.md](docs/planning/sds.md) — Software Design Specification (authoritative technical design)
- [setup-instructions.md](setup-instructions.md) — Extended Docker setup reference
