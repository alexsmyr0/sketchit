import os
import re

path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the accumulation loop to wait for the specific type sent
content = content.replace(
    'await sync_viewer.receive_json_from()',
    'await _receive_until_type(sync_viewer, s["type"])'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("test_drawing.py accumulation loop fixed.")
