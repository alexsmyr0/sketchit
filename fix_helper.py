import re

with open('rooms/tests/test_consumers.py', 'r') as f:
    c = f.read()

c = re.sub(
    r'async def _connect_and_drain_initial_sync.*?return buffered',
    '''async def _connect_and_drain_initial_sync(communicator, join_code: str, expects_game_active: bool = False, timeout: float = 3.0):
    await _connect_and_receive_initial_room_state(communicator, join_code)
    import json, asyncio
    buffered = []
    while True:
        try:
            raw = communicator.output_queue.get_nowait()
            if raw.get("type") == "websocket.send":
                buffered.append(json.loads(raw["text"]))
        except asyncio.QueueEmpty:
            break
    allowed_types = {"drawing.stroke", "drawing.end_stroke", "drawing.clear", "round.state", "round.started", "room.state", "round.timer"}
    for f in buffered:
        typ = str(f.get("type"))
        if typ not in allowed_types and not typ.startswith(("drawing.", "round.")):
            raise AssertionError(f"Unexpected connect-time event: {f}")
    return buffered''',
    c,
    flags=re.DOTALL
)

with open('rooms/tests/test_consumers.py', 'w') as f:
    f.write(c)
