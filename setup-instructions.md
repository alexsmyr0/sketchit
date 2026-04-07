# Sketchit Setup Instructions

This project uses Docker Compose to run the Django app together with its required backing services:

- MySQL for persistent application data
- Redis for the Channels message broker and WebSocket communication

The supported local setup path is Docker Compose.

## Prerequisites

- Docker Desktop installed and running
- Docker Compose available through `docker compose`

## Services in This Project

`app`
- Runs the Django project
- Applies migrations on startup
- Serves the ASGI application with Daphne on port `8000`

`mysql`
- Stores persistent data such as Django models and future game data
- Replaces the old SQLite development database

`redis`
- Backs Django Channels
- Lets different app processes share WebSocket and real-time messages
- Needed for future multiplayer features

## Start the Project

From the project root, build and start all services:

```bash
docker compose up --build
```

What happens on startup:

1. Docker builds the Django image
2. MySQL and Redis start first
3. The Django container waits for those services to be healthy
4. Django runs `python manage.py migrate`
5. Daphne starts the ASGI app at `http://127.0.0.1:8000`

To start the stack in the background:

```bash
docker compose up --build -d
```

To stop the stack:

```bash
docker compose down
```

To stop the stack and remove database and Redis volumes:

```bash
docker compose down -v
```

Use `down -v` only when you want to delete local MySQL and Redis data.

## Apply Migrations Manually

The app container already runs migrations automatically on startup, but you can also run them manually:

```bash
docker compose exec app python manage.py migrate
```

If you add or change models, create migrations with:

```bash
docker compose exec app python manage.py makemigrations
docker compose exec app python manage.py migrate
```

## Useful Commands

View running services:

```bash
docker compose ps
```

View logs:

```bash
docker compose logs -f
```

Open a shell in the app container:

```bash
docker compose exec app sh
```

## Run Tests

Start the backing services first:

```bash
docker compose up -d mysql redis
```

Run the full test suite with the Docker-specific test settings:

```bash
docker compose run --rm app python manage.py test --settings=config.test_settings
```

Run a smaller subset of tests:

```bash
docker compose run --rm app python manage.py test rooms.tests games.tests --settings=config.test_settings
```

Important notes:

- `config.test_settings` is intended to be used from Docker, not from the host shell.
- The test settings force `MYSQL_HOST=mysql` and Docker test credentials, so host-side commands like `python manage.py test --settings=config.test_settings` are not the supported path.
- `config.test_settings` still uses MySQL, but swaps Channels to the in-memory layer so tests that do not need Redis transport behavior can run without Redis itself being under test.

## Environment Configuration

The Docker Compose file currently provides the required environment variables for local development, including:

- `MYSQL_DATABASE`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_HOST`
- `MYSQL_PORT`
- `REDIS_URL`

The Django project runtime is configured to require MySQL and Redis. SQLite is no longer part of the active project setup.

## Why Both MySQL and Redis?

They solve different problems.

MySQL:
- Stores durable data
- Keeps data after the app restarts
- Handles normal Django database work

Redis:
- Handles fast temporary messaging between app processes
- Supports Django Channels
- Makes WebSockets and live multiplayer communication possible

Redis is not the main database here. MySQL remains the source of truth for saved data.

## Troubleshooting

If `docker compose up` fails immediately:

- Make sure Docker Desktop is running
- Check that ports `8000`, `3306`, and `6379` are not already in use
- Run `docker compose logs -f` to see which service failed

If the app fails because MySQL or Redis is unavailable:

- Run `docker compose ps` and confirm both `mysql` and `redis` are healthy
- Restart the stack with `docker compose down` and `docker compose up --build`

## Current Entry Points

- App: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- Admin: [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)
