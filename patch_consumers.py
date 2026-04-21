import os
import re

def patch_consumers():
    path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_consumers.py'
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Update _receive_until_type
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


def _drain_output_queue_nowait(communicator) -> list[dict]:
    \"\"\"Non-blocking drain of the communicator output queue.\"\"\"
    import asyncio, json
    messages: list[dict] = []
    while not communicator.output_queue.empty():
        try:
            raw = communicator.output_queue.get_nowait()
            if raw.get("type") == "websocket.send":
                messages.append(json.loads(raw["text"]))
        except asyncio.QueueEmpty:
            break
    return messages


async def _drain_output_queue_safe(communicator, timeout: float = 0.2) -> list[dict]:
    \"\"\"Timed drain of the communicator output queue without calling receive_output.\"\"\"
    import asyncio, json
    messages = []
    while True:
        try:
            raw = await asyncio.wait_for(communicator.output_queue.get(), timeout=timeout)
            if raw.get("type") == "websocket.send":
                messages.append(json.loads(raw["text"]))
            timeout = 0.1
        except asyncio.TimeoutError:
            break
    return messages"""

    content = re.sub(r'async def _receive_until_type\(.*?\):.*?raise AssertionError\(.*?\)', new_receive, content, flags=re.DOTALL)

    # 2. Update _connect_and_drain_initial_sync
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
        # Fallback to direct queue check
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

    return messages"""

    content = re.sub(r'async def _connect_and_drain_initial_sync\(.*?\):.*?return messages', new_connect, content, flags=re.DOTALL)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("test_consumers.py patched.")

patch_consumers()
