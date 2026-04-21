import os
import re

path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix test_drawing_end_stroke_broadcast
content = content.replace(
    'response = await _receive_until_type(viewer_socket, "drawing.clear")\n        self.assertEqual(response["type"], "drawing.end_stroke")',
    'response = await _receive_until_type(viewer_socket, "drawing.end_stroke")'
)

# Fix any other accidental "drawing.clear" replacements that should be "drawing.stroke"
# I'll just check line by line
lines = content.splitlines()
new_lines = []
for line in lines:
    if 'await _receive_until_type(viewer_socket, "drawing.clear")' in line:
        # Check if previous lines sent a stroke
        # Actually, I'll just be more careful
        pass
    new_lines.append(line)

# Let's just fix the specific ones I know are wrong
content = content.replace(
    'response = await _receive_until_type(viewer_socket, "drawing.clear")',
    'response = await _receive_until_type(viewer_socket, "drawing.stroke")'
)
# Wait, this might be wrong for the ACTUAL clear test.
# Revert that one
content = content.replace(
    '# Viewer should receive the clear event broadcast\n        # Room.state arrival broadcasts were drained by handshake, so clear should be next\n        response = await _receive_until_type(viewer_socket, "drawing.stroke")',
    '# Viewer should receive the clear event broadcast\n        # Room.state arrival broadcasts were drained by handshake, so clear should be next\n        response = await _receive_until_type(viewer_socket, "drawing.clear")'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("test_drawing.py surgical fix 2 applied.")
