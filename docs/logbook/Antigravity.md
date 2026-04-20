# Antigravity's Logbook

## 2026-04-20
### G-02: Live Lobby Page Template & Room Client
Implemented the real-time lobby for Sketchit. 
- Integrated backend broadcasts for room state changes (settings updates, game start, participant connections).
- Redesigned the lobby UI with a premium, modern look.
- Implemented `room_lobby.js` for WebSocket synchronization and dynamic UI updates (participant list, room metadata, host controls).
- Added "Copy Join URL" functionality for easy room sharing.
- Ensured host-only controls are correctly restricted and functional.
