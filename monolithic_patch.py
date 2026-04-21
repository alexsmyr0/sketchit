import os
import re

def monolithic_patch():
    # 1. test_consumers.py - Robust Helpers
    path_c = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_consumers.py'
    with open(path_c, 'r', encoding='utf-8') as f:
        content_c = f.read()
    
    # Safe _receive_until_type
    new_receive = """async def _receive_until_type(communicator, event_type: str, attempts: int = 40):
    \"\"\"Wait for and return a specific JSON event type, ignoring others (safe version).\"\"\"
    import asyncio, json
    for _ in range(attempts):
        try:
            # Use wait_for on the queue directly to avoid CancelledError on the app task
            raw = await asyncio.wait_for(communicator.output_queue.get(), timeout=2.0)
            if raw.get("type") == "websocket.send":
                event = json.loads(raw["text"])
                if event.get("type") == event_type:
                    return event
        except asyncio.TimeoutError:
            continue
    raise AssertionError(f"Did not receive expected event type '{event_type}'.")
"""
    content_c = re.sub(r'async def _receive_until_type\(.*?\):.*?raise AssertionError\(.*?\)', new_receive, content_c, flags=re.DOTALL)
    
    # Safe _connect_and_drain_initial_sync
    new_connect = """async def _connect_and_drain_initial_sync(
    communicator: WebsocketCommunicator,
    join_code: str,
    expects_game_active: bool = False,
    timeout: float = 5.0,
) -> list[dict]:
    \"\"\"Connect to a room and safely collect all initial A-06 handshake events.\"\"\"
    import asyncio
    connected, _ = await communicator.connect(timeout=timeout)
    if not connected:
        raise ConnectionError(f"Failed to connect to room {join_code}")

    # The first message is always the direct room.state snapshot.
    try:
        first_msg = await communicator.receive_json_from(timeout=3)
    except asyncio.TimeoutError:
        # Fallback to direct queue check if receive fails (rare but helps determinism)
        burst = await _drain_output_queue_safe(communicator, timeout=0.5)
        if not burst:
             raise ConnectionError(f"Handshake timeout in room {join_code}")
        first_msg = burst[0]
        
    messages = [first_msg]

    # If we expect a game, we should wait until we see the round.timer or round.state
    if expects_game_active:
        for _ in range(20):
            burst = await _drain_output_queue_safe(communicator, timeout=0.2)
            messages.extend(burst)
            if any(m.get("type") in ("round.timer", "round.intermission_timer", "round.state") for m in burst):
                break
            await asyncio.sleep(0.1)
    else:
        burst = await _drain_output_queue_safe(communicator, timeout=0.3)
        messages.extend(burst)

    return messages
"""
    content_c = re.sub(r'async def _connect_and_drain_initial_sync\(.*?\):.*?return messages', new_connect, content_c, flags=re.DOTALL)
    
    with open(path_c, 'w', encoding='utf-8') as f:
        f.write(content_c)

    # 2. test_drawing.py - Robust Tests
    path_d = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
    with open(path_d, 'r', encoding='utf-8') as f:
        content_d = f.read()

    # Fix sequence numbers in setUp
    content_d = re.sub(r'sequence_number=\d+', 'sequence_number=1', content_d)

    # Fix test_snapshot_accumulation (with 4 spaces indentation for the class method)
    # Actually, the file uses 4 spaces for indentation.
    new_accumulation = """    async def test_snapshot_accumulation(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        # Viewer to synchronize processing
        sync_viewer = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=True)
        await _connect_and_drain_initial_sync(sync_viewer, self.room.join_code, expects_game_active=True)

        # Send 2 strokes and an end_stroke
        strokes = [
            {"type": "drawing.stroke", "payload": {"id": 1}},
            {"type": "drawing.stroke", "payload": {"id": 2}},
            {"type": "drawing.end_stroke", "payload": {}},
        ]
        for s in strokes:
            await drawer_socket.send_json_to(s)
            # Wait for broadcast to ensure Redis is updated
            await _receive_until_type(sync_viewer, s["type"])
        
        # Connect a new viewer
        new_viewer_key = await _create_room_member(self.room.id, "Late Bob")
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(new_viewer_key),
        )
        
        # Replayed messages are captured during handshake
        messages = await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)

        # Filter for drawing events
        replayed_strokes = [m for m in messages if m.get("type", "").startswith("drawing.")]
        self.assertEqual(replayed_strokes, strokes)

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()
        await sync_viewer.disconnect()
"""
    # Note: re.sub will use the exact string. If the target was indented 8 spaces, I should indent my replacement 8 spaces.
    # Wait! test_drawing.py uses 4 spaces for class methods.
    
    content_d = re.sub(r'    async def test_snapshot_accumulation\(self\):.*?await viewer_socket\.disconnect\(\)', new_accumulation, content_d, flags=re.DOTALL)

    # Fix test_snapshot_isolation_between_rounds
    new_isolation = """    async def test_snapshot_isolation_between_rounds(self):
        self._seed_active_round_state()
        # 1. Start round, send drawing
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=True)
        # Viewer to synchronize processing
        sync_viewer = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        await _connect_and_drain_initial_sync(sync_viewer, self.room.join_code, expects_game_active=True)

        await drawer_socket.send_json_to({"type": "drawing.stroke", "payload": {"test": 1}})
        # Wait for broadcast
        await _receive_until_type(sync_viewer, "drawing.stroke")
        
        # Verify snapshot exists
        self.assertEqual(len(room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code)), 1)
        
        # 2. Simulate round end
        from channels.db import database_sync_to_async
        game_word = await database_sync_to_async(GameWord.objects.create)(
            game=self.game, text="rocket"
        )
        round_obj = await database_sync_to_async(Round.objects.create)(
            game=self.game,
            sequence_number=2,
            drawer_participant=self.drawer_player,
            drawer_nickname=self.drawer_player.display_name,
            selected_game_word=game_word,
        )
        await database_sync_to_async(game_services.complete_round_due_to_timer)(round_obj.id)
        
        # 3. New viewer connects during intermission
        new_viewer_key = await _create_room_member(self.room.id, "Intermission Bob")
        v_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(new_viewer_key),
        )
        # Manually seed intermission turn state
        game_redis.set_turn_state(self.fake_redis, self.room.join_code, {
            "phase": "intermission",
            "round_id": str(round_obj.id)
        })

        await _connect_and_drain_initial_sync(v_socket, self.room.join_code, expects_game_active=True)
        
        # Viewer should receive NOTHING drawing-related
        drawn = [m for m in _drain_output_queue_nowait(v_socket) if m.get("type", "").startswith("drawing.")]
        self.assertEqual(drawn, [])

        await drawer_socket.disconnect()
        await sync_viewer.disconnect()
        await v_socket.disconnect()
"""
    content_d = re.sub(r'    async def test_snapshot_isolation_between_rounds\(self\):.*?await viewer_socket\.disconnect\(\)', new_isolation, content_d, flags=re.DOTALL)

    with open(path_d, 'w', encoding='utf-8') as f:
        f.write(content_d)

    # 3. test_guesses.py - Robust Tests
    path_g = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_guesses.py'
    with open(path_g, 'r', encoding='utf-8') as f:
        content_g = f.read()

    content_g = re.sub(r'sequence_number=\d+', 'sequence_number=1', content_g)
    
    with open(path_g, 'w', encoding='utf-8') as f:
        f.write(content_g)

    print("Monolithic patch applied with proper indentation.")

monolithic_patch()
