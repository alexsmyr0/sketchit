## 09/03

Implemented issue #3 by creating the new Django project structure in the repository, including `manage.py`, the main project configuration files, and the first app folders for `core`, `rooms`, `games`, and `words`, so the project could start and run without crashing.

## 21/03

Implemented issue #9 by adding the `Game` and `Round` database models to represent a match and the rounds inside it, with the core structure needed for overall match state, current drawer, chosen word, timing, and round status.

## 31/03

Implemented issue #5 by creating the backend Dockerfile for the Django app and defining the basic team workflow for starting the app, running migrations, and running tests, along with setup guidance for the team.

## 01/04

Implemented issue #6 by configuring the Django project to use MySQL instead of SQLite, adding Django Channels, configuring Redis as the channel layer, and preparing the ASGI setup so the backend was ready for future WebSocket-based multiplayer features.

## 04/04

Implemented issue #4 by creating the Docker Compose setup for the Django app, MySQL, and Redis so the team could run the full local stack together through shared containers on one Docker network.

## 05/04

Implemented issue #18 by adding starter seed data through a basic word pack and writing setup documentation that explains how to run the project with Docker, apply migrations, and understand the roles of MySQL and Redis in the system.

## 06/04

Implemented issue #24 by building the backend room-creation flow for guest hosts, including the create-room endpoint, unique `join_code` generation, lobby room creation, session persistence, and host participant creation tied to the request session.

## 06/04

Implemented issue #25 by building the room join flow using `join_code` and Django session identity, including participant create-or-reuse behavior, room-capacity enforcement, prevention of one session joining multiple rooms, and nickname persistence on first join.

## 08/04

Removed the remaining SQLite usage from the active project setup by deleting the SQLite test database override, removing stray `db.sqlite3` and `test.sqlite3` files, and updating the documentation so the repo now clearly states that MySQL is the database target for development, runtime, and tests. Also removed a redundant example code directory we were using to take inspiration for some database functionality

## 10/04
Graded and suggested changes for ticket NO3 for Nikos. He implemented my suggestions and I approved and merged.

## 11/04
Reviewed two pull requests from Kostas and Nikos regarding the respective tickets K03 and NO4. Found functionality and testing gaps. I commented on the pull quest.
Both implemented my suggestions and I have now approved and merged. 