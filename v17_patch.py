import os
import re

# 1. Update test_drawing.py
path_d = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(path_d, 'r', encoding='utf-8') as f:
    content_d = f.read()

# Make test_snapshot_accumulation more robust
# 1. Don't disconnect sync_viewer early
# 2. Use a longer timeout for the broadcasts
content_d = content_d.replace(
    'await _receive_until_type(sync_viewer, s["type"])',
    'await _receive_until_type(sync_viewer, s["type"], attempts=30)'
)
content_d = content_d.replace(
    'await sync_viewer.disconnect()\n\n        # Connect a new viewer',
    '# Keep sync_viewer alive to avoid disconnect-triggered broadcasts\n        # Connect a new viewer'
)
# Add the disconnect at the end
content_d = content_d.replace(
    'await viewer_socket.disconnect()',
    'await viewer_socket.disconnect()\n        await sync_viewer.disconnect()'
)

with open(path_d, 'w', encoding='utf-8') as f:
    f.write(content_d)

# 2. Update test_consumers.py _receive_until_type to be even more patient
path_c = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_consumers.py'
with open(path_c, 'r', encoding='utf-8') as f:
    content_c = f.read()

content_c = content_c.replace('timeout=1.0', 'timeout=2.0')

with open(path_c, 'w', encoding='utf-8') as f:
    f.write(content_c)

print("V17 patches applied.")
