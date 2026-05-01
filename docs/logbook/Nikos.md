## 06/04

Implemented issue #13 by adding the initial Redis room presence and canvas snapshot state management, laying the groundwork for real-time state synchronization and recovery.

## 08/04

Implemented issue #15 by building the room join/create HTML entry pages, and issue #28 by developing the session-aware room WebSocket consumer and routing infrastructure for live player interactions.

## 09/04

Completed ticket N-02 by refining the presence state logic and adding comprehensive unit tests for the room consumer to ensure robust real-time synchronization.

## 10/04

Completed ticket N-03 by implementing a Redis-based Round Runtime state layer with Fakeredis validation, and updated the guess state API to handle complex payload structures for enriched history tracking.

## 11/04

Completed ticket N-04 by developing the drawing event broadcast system and canvas snapshot synchronization, ensuring that drawing data is correctly propagated to all participants and preserved for late joiners.

### 20/04
**G-02: Live Lobby Page Template & Room Client**
Implemented the real-time lobby for Sketchit. 
- Integrated backend broadcasts for room state changes (settings updates, game start, participant connections).
- Redesigned the lobby UI with a premium, modern look.
- Implemented `room_lobby.js` for WebSocket synchronization and dynamic UI updates (participant list, room metadata, host controls).
- Added "Copy Join URL" functionality for easy room sharing.
- Ensured host-only controls are correctly restricted and functional.

## 28/04
made it so lobby disconnect now permanently removes participant row

Disconnecting from a lobby room routes through leave_participant instead of just stamping DISCONNECTED on the row. The stale membership row was blocking the session from joining or creating another room with a 409 conflict. Host reassignment and empty-room grace fire correctly on departure.

## 30/04
Added Leave button to lobby settings header
Added a Leave button to the right of the Lobby Settings title. Clicking it POSTs to the new rooms/<join_code>/leave/ endpoint and redirects to the entry page. The button is disabled on click to prevent double-submission.