import re

def fix_consumers():
    with open('rooms/tests/test_consumers.py', 'r') as f:
        c = f.read()

    p_sync = re.compile(
        r'async def _connect_and_drain_initial_sync\(\s*communicator,\s*join_code: str,\s*\):\s*\"\"\"Connect.*?return buffered',
        re.DOTALL
    )

    new_sync = '''async def _connect_and_drain_initial_sync(
    communicator,
    join_code: str,
    expects_game_active: bool = False,
    timeout: float = 3.0,
):
    """Connect and safely discard active game sync noise explicitly (A-06)."""
    await _connect_and_receive_initial_room_state(communicator, join_code)
    
    # Safely clear the queue of game sync events instead of timing out
    # We assert that ONLY sync events are drained here.
    buffered = _drain_output_queue_nowait(communicator)
    allowed_types = ("drawing.stroke", "drawing.end_stroke", "drawing.clear", "round.state", "round.started", "room.state", "round.timer")
    for f in buffered:
        typ = str(f.get("type"))
        if typ not in allowed_types and not typ.startswith(("drawing.", "round.")):
            raise AssertionError(f"Unexpected connect-time event: {f}")
            
    return buffered'''
    
    # We must also do the first replace if needed.
    c = p_sync.sub(new_sync, c)

    with open('rooms/tests/test_consumers.py', 'w') as f:
        f.write(c)

fix_consumers()
