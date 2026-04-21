import os
import re

path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix Bug 1: Wait for any drawing event during accumulation
content = content.replace(
    'await _receive_until_type(sync_viewer, "drawing.stroke")',
    'await sync_viewer.receive_json_from()' # Use raw receive here because we know exactly what we expect
)

# Fix Bug 2: Wait for correct types in accumulation loop
content = content.replace(
    'response = await _receive_until_type(viewer_socket, "drawing.clear")',
    'response = await _receive_until_type(viewer_socket, expected["type"])'
)

# Fix Bug 1 again (for the loop)
content = content.replace(
    'await _receive_until_type(sync_viewer, "drawing.stroke")', # If it missed it
    'await sync_viewer.receive_json_from()'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("test_drawing.py final final FINAL fix applied.")
