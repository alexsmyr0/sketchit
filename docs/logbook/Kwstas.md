## Mar 21

- Added Wordpack, word, and guess models.
- Created player room.

## Apr 6

- Connected domain models to Django admin and added admin config tests.

## Apr 7

- Added lobby room-state loading for joined participants.
- Added a room-to-word-pack relation and default word-pack assignment.
- Added server-side game.
- Added host-only start-game action.

## Apr 8

- Added server-side round-resolution logic.
- Added drawer rotation.

## Apr 9

- Added public room directory API.
- Added entry screen and public room discovery UI.

## Apr 10

- Added round timer and early-finish coordinator.

## Apr 13

- Completed full game cycle drawer rotation.

## Apr 15

- Added Time-Based Scoring & Multi-Guesser Round Resolution.

## Apr 21

- Added Drawer Disconnect Grace & Turn Outcome Handling.
## Apr 20

- Added near-match, duplicate, and correct-once guess rules.

## Apr 29
Lowered Grace period for empty rooms from 10 minutes to 1. When a room has 0 participants it will now be deleted after 1 minute.

Fixed a bug where after the grace period, a room that is deleted is still visible to players in the homepage as a "zombie" entry. Clicking join returned a 404. Now the UI gets updated accordingly.