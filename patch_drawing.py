import os, re
path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace common patterns
content = re.sub(r'response = await (\w+)\.receive_json_from\(\)\s+self\.assertEqual\(response\["type"\], "(.*?)"\)', r'response = await _receive_until_type(\1, "\2")', content)
content = re.sub(r'resp1 = await (\w+)\.receive_json_from\(\)\s+resp2 = await (\w+)\.receive_json_from\(\)', r'resp1 = await _receive_until_type(\1, "drawing.stroke")\n        resp2 = await _receive_until_type(\2, "drawing.stroke")', content)

# Specific fix for test_snapshot_accumulation replayed messages
content = content.replace(
    'for expected in strokes:\n            response = await viewer_socket.receive_json_from()\n            self.assertEqual(response, expected)',
    'messages = await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)\n        replayed = [m for m in messages if m.get("type", "").startswith("drawing.")]\n        self.assertEqual(replayed, strokes)'
)
# Avoid double connect in test_snapshot_accumulation
content = content.replace(
    'await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)\n        messages = await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)',
    'messages = await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print('Regex patched receive in test_drawing.py')
