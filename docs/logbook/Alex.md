## 03/03
Added packages for base apps, creating the scapfolding for the project

## 06/03
Added logbooks for everyone so we can track our progress.

## 09/03

Implemented issue #3 by creating the new Django project structure in the repository, including `manage.py`, the main project configuration files, and the first app folders for `core`, `rooms`, `games`, and `words`, so the project could start and run without crashing.

## 18/03

Added markdown files for decision-log, issue creation guide, project context, and team-workflow

## 21/03

Implemented issue #9 by adding the `Game` and `Round` database models to represent a match and the rounds inside it, with the core structure needed for overall match state, current drawer, chosen word, timing, and round status.

Reviewed Kosta's PR regarding implementation of issue 8 (Create the Player and Room database models) and approved

## 22/03

Reviewed Kosta's PR regarding implementation of issue 10 (add Wordpack, word , and guess models) and approved


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

Implemented issue #25 by building the room join flow using `join_code` and Django session identity, including participant create-or-reuse behavior, room-capacity enforcement, prevention of one session joining multiple rooms, and nickname persistence on first join.

Reviewed Kosta's PR regarding implementation of issue 26 (Added lobby room-state loading for joined participants) and approved

Reviewed Kosta's PR regarding implementation of issue 11 (Connect domain models to Django admin and add admin config tests) and approved



## 07/04
Removed Sqlite mentions entirely in favor of mysql, cleaned up old example code we don't need and updated markdowns accordingly

Reviewed Kosta's PR regarding implementation of issue 40 (Added a room-to-word-pack relation and default word-pack assignment) and approved

Reviewed Kosta's PR regarding implementation of issue 32 (added server side game) and recommended fixes for some bugs and shortcomings testing wise. He implemented them and I approved.


## 08/04

Removed the remaining SQLite usage from the active project setup by deleting the SQLite test database override, removing stray `db.sqlite3` and `test.sqlite3` files, and updating the documentation so the repo now clearly states that MySQL is the database target for development, runtime, and tests. Also removed a redundant example code directory we were using to take inspiration for some database functionality

## 10/04
Graded and suggested changes for ticket NO3 for Nikos. He implemented my suggestions and I approved and merged.

## 11/04
Reviewed two pull requests from Kostas and Nikos regarding the respective tickets K03 and NO4. Found functionality and testing gaps. I commented on the pull quest.
Both implemented my suggestions and I have now approved and merged.

Implemented A05 empty-room grace: empty rooms now enter empty_grace, persist empty_since, store a Redis cleanup deadline, restore cleanly to lobby on timely rejoin, cancel abandoned game state instead of resuming it, and hard-delete after the grace period through the cleanup flow.

## 12/04
Graded N4 and N5 ticket for Nikos. There were critical bugs in both but after pointing them out, Nikos patched them up swiftly and I merged.

# 14/04
Implemented A6. Implemented server-authoritative live lobby WebSocket updates by sending initial and change-driven room.state snapshots, plus host.changed on real host handoffs, for joins, leaves, disconnects, and reconnects.

## 15/04
Graded Kostas's K4 ticket regarding full game-cycle drawer rotation and word uniqueness. I suggested a small fix, he implemented it, and I then approved and merged the PR.
Updated implementation documentation and issue-PR templates

## 18/04
Graded Nikos's PR #69. I found a few issues, reported them, and Nikos patched them swiftly. I then reassessed the updated work and merged it.

## 19/04
Graded Nikos's pull requests #77, #76, and #75. I found bugs in all three, reported them to Nikos, and he resolved them. I then reassessed the updated work and pushed them forward.

## 20/04
Implemented A7, which adds the mid-game joiner and reconnect reclaim rules so players who join during an active game become spectators for the current turn, reconnecting same-session players keep their original place and score, and the room behavior stays consistent with the SDS for session-based guest identity.
Nikos graded my implementation and found some testing gaps and one critical bug. After discussing it with him, I fixed the problems, and he merged the ticket.
Graded G2 and G3 implementation ticket by Nikos. There were a few critical bugs I uncovered and testing coverage limitations. After my review he patched G2 shortly but G3 remains incomplete.

## 21/04
Implemented issue #79 by fixing the dead-end room entry flow for stale or hidden guest-session room ownership. Expired same-session participant rows are now cleaned up before create/join rejects the request, valid existing ownership returns a recoverable response with `room_url`, the entry-page JavaScript redirects guests back into their existing room for that recoverable case, and room entry is now serialized by Django session to prevent concurrent duplicate room ownership.
Graded Kostas's K6 ticket and gave feedback. After reassessing the updated implementation, I merged it.

## 22/04
Graded Niko's G3 implementation and found he fixed the original problem but reintroduced a new bug. Gave him a comment

## 23/04
Reviewed Niko's G3 implementation and it now passes. Merged.
Reviewed Kosta's K7 implementation where he handles drawer disconnect grace & turn outcome handling. There were a few issues and a part of the implementation was incomplete

## 24/04
Looked into Kosta's K7 patches and it now passes. I merged shortly

## 26/04
Implemented G4 where I wired drawing canvas to the room socket. Got graded by Kwstas and my implementation passes

## 28/04
Fixed a bug where we were accidentally still using sqlite to run some tests and standardized tests on MySQL

## 29/04
Patched a bug where creating a room presented a room intermission overlay that was wrong as the room was in lobby state and brand new. The problem was CSS Display:flex overriding [hidden] attribute causing the overlay to linger forever

## 30/04
Patched a bug where entering the join code from another room and joining, was calling create room instead of join room and was just creating a new room.

## 01/05
Lobby settings were visible to non admins of a room (Even though they couldn't edit them). I removed them so the UI looks cleaner.

