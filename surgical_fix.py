import os
import re

path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the accidental replacement in test_drawing_clear_deletes_redis_snapshot
content = content.replace(
    'response = await _receive_until_type(viewer_socket, expected["type"])',
    'response = await _receive_until_type(viewer_socket, "drawing.clear")'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("test_drawing.py surgical fix applied.")
