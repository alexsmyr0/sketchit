import os
import re

path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the blocking receives in test_snapshot_accumulation
content = content.replace(
    'await sync_viewer.receive_json_from()',
    'await _receive_until_type(sync_viewer, "drawing.stroke")'
)

# Also fix the last one in that test
content = content.replace(
    'await sync_viewer.receive_json_from()', # If it's already replaced once, this won't match the same spot
    'await _receive_until_type(sync_viewer, "drawing.stroke")'
)

# And for drawing.clear
content = content.replace(
    'await viewer_socket.receive_json_from()',
    'await _receive_until_type(viewer_socket, "drawing.clear")'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Final, final fix for drawing.py applied.")
