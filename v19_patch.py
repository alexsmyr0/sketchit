import os
import re

path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Fix NameError in test_snapshot_isolation_between_rounds
content = content.replace(
    'await _receive_until_type(sync_viewer, s["type"])',
    'await _receive_until_type(sync_viewer, "drawing.stroke")'
)

# 2. Fix test_snapshot_accumulation to check replayed messages instead of waiting for them again
bad_accumulation_check = """        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)

        # Should receive all 3 messages in order
        for expected in strokes:
            response = await _receive_until_type(viewer_socket, expected["type"])
            self.assertEqual(response, expected)"""

good_accumulation_check = """        messages = await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)

        # Should receive all 3 messages in order (they are replayed during handshake)
        replayed_strokes = [m for m in messages if m.get("type", "").startswith("drawing.")]
        self.assertEqual(replayed_strokes, strokes)"""

content = content.replace(bad_accumulation_check, good_accumulation_check)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("V19 patch applied.")
