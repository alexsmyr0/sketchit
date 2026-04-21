import os
import json

def patch_file(path, search, replace, count=1):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    new_content = content.replace(search, replace, count)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)

# 1. Fix SnapshotSyncTests sequence number in setUp
test_drawing_path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(test_drawing_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# SnapshotSyncTests.setUp is around line 347
for i in range(340, 380):
    if i < len(lines) and 'sequence_number=2' in lines[i]:
        lines[i] = lines[i].replace('sequence_number=2', 'sequence_number=1')
        print(f"Fixed SnapshotSyncTests.setUp at line {i+1}")
        break

with open(test_drawing_path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

# 2. Fix test_guesses.py missing seeds and ensure drain helper
test_guesses_path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_guesses.py'
with open(test_guesses_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'async def test_near_match_outcome_broadcast(self):' in line:
        if '_seed_active_round_state' not in lines[i+1]:
            lines.insert(i+1, '        self._seed_active_round_state()\n')
            print("Added seed to test_near_match_outcome_broadcast")
    if 'async def test_duplicate_outcome_broadcast(self):' in line:
        if '_seed_active_round_state' not in lines[i+1]:
            lines.insert(i+1, '        self._seed_active_round_state()\n')
            print("Added seed to test_duplicate_outcome_broadcast")

with open(test_guesses_path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

# 3. Enhance helper in test_consumers.py
test_consumers_path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_consumers.py'
with open(test_consumers_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Make _connect_and_drain_initial_sync more robust by waiting for round.timer if expects_game_active
new_helper = """async def _connect_and_drain_initial_sync(
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
        for _ in range(10):
            burst = await _drain_output_queue_safe(communicator, timeout=0.1)
            messages.extend(burst)
            if any(m.get("type") in ("round.timer", "round.state") for m in burst):
                break
            await asyncio.sleep(0.1)
    else:
        burst = await _drain_output_queue_safe(communicator, timeout=0.2)
        messages.extend(burst)

    return messages"""

import re
content = re.sub(r'async def _connect_and_drain_initial_sync\(.*?\)\s*->\s*list\[dict\]:.*?return \[first_msg\] \+ burst', new_helper, content, flags=re.DOTALL)

with open(test_consumers_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched successfully.")
