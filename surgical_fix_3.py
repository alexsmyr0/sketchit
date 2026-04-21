import os
import re

path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the accidental s["type"] in test_snapshot_isolation_between_rounds
content = content.replace(
    'await drawer_socket.send_json_to({"type": "drawing.stroke", "payload": {"test": 1}})\n        # Wait for broadcast\n        await _receive_until_type(sync_viewer, s["type"])',
    'await drawer_socket.send_json_to({"type": "drawing.stroke", "payload": {"test": 1}})\n        # Wait for broadcast\n        await _receive_until_type(sync_viewer, "drawing.stroke")'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("test_drawing.py surgical fix 3 applied.")
