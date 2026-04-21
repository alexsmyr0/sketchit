import os
import re

def replace_block(content, start_marker, end_marker, replacement):
    pattern = re.escape(start_marker) + r'.*?' + re.escape(end_marker)
    return re.sub(pattern, replacement, content, flags=re.DOTALL)

# 1. Update test_consumers.py
test_consumers_path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_consumers.py'
with open(test_consumers_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Add _drain_output_queue_nowait
helper_drain_nowait = """def _drain_output_queue_nowait(communicator) -> list[dict]:
    \"\"\"Non-blocking drain of the communicator output queue.\"\"\"
    messages: list[dict] = []
    while not communicator.output_queue.empty():
        try:
            raw = communicator.output_queue.get_nowait()
            if raw.get("type") == "websocket.send":
                messages.append(json.loads(raw["text"]))
        except asyncio.QueueEmpty:
            break
    return messages
"""
if '_drain_output_queue_nowait' not in content:
    content = content.replace('async def _drain_output_queue_safe', helper_drain_nowait + '\n\nasync def _drain_output_queue_safe')

# Update _connect_and_drain_initial_sync to look for intermission timer too
new_connect_helper = """async def _connect_and_drain_initial_sync(
    communicator: WebsocketCommunicator,
    join_code: str,
    expects_game_active: bool = False,
    timeout: float = 3.0,
) -> list[dict]:
    \"\"\"Connect to a room and safely collect all initial A-06 handshake events.\"\"\"
    connected, _ = await communicator.connect(timeout=timeout)
    if not connected:
        raise ConnectionError(f"Failed to connect to room {join_code}")

    # The first message is always the direct room.state snapshot.
    first_msg = await communicator.receive_json_from(timeout=2)
    messages = [first_msg]

    # If we expect a game, we should wait until we see the round.timer or round.state
    # This ensures we've finished the async handshake loop in the consumer.
    if expects_game_active:
        # We wait up to 1s for the game state to arrive
        for _ in range(15):
            burst = await _drain_output_queue_safe(communicator, timeout=0.1)
            messages.extend(burst)
            if any(m.get("type") in ("round.timer", "round.intermission_timer", "round.state") for m in burst):
                break
            await asyncio.sleep(0.1)
    else:
        burst = await _drain_output_queue_safe(communicator, timeout=0.2)
        messages.extend(burst)

    return messages"""

content = re.sub(r'async def _connect_and_drain_initial_sync\(.*?\)\s*->\s*list\[dict\]:.*?return messages', new_connect_helper, content, flags=re.DOTALL)

with open(test_consumers_path, 'w', encoding='utf-8') as f:
    f.write(content)

# 2. Update test_drawing.py
test_drawing_path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(test_drawing_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Import the new helper
content = content.replace('_connect_and_drain_initial_sync', '_connect_and_drain_initial_sync, _drain_output_queue_nowait')

# Fix SnapshotSyncTests.setUp sequence number
# First, revert any previous mess
content = re.sub(r'class SnapshotSyncTests.*?sequence_number\s*=\s*\d+', lambda m: m.group(0).split('=')[0] + '= 1', content, count=1, flags=re.DOTALL)
# Fix test_snapshot_isolation_between_rounds to use 2
content = re.sub(r'test_snapshot_isolation_between_rounds.*?sequence_number\s*=\s*\d+', lambda m: m.group(0).split('=')[0] + '= 2', content, count=1, flags=re.DOTALL)

with open(test_drawing_path, 'w', encoding='utf-8') as f:
    f.write(content)

# 3. Update test_guesses.py
test_guesses_path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_guesses.py'
with open(test_guesses_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Import the new helper
content = content.replace('_connect_and_drain_initial_sync', '_connect_and_drain_initial_sync, _drain_output_queue_nowait')

with open(test_guesses_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Final patch v4 applied.")
