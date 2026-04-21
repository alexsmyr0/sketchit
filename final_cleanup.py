import os
import re

# 1. Fix test_drawing.py to use _receive_until_type for broadcasts
test_drawing_path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(test_drawing_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace direct receives with _receive_until_type
content = content.replace(
    'response = await viewer_socket.receive_json_from()\n        self.assertEqual(response["type"], "drawing.stroke")',
    'response = await _receive_until_type(viewer_socket, "drawing.stroke")'
)
# And for multiple viewers
content = content.replace(
    'response = await v.receive_json_from()\n            self.assertEqual(response["type"], "drawing.stroke")',
    'response = await _receive_until_type(v, "drawing.stroke")'
)

with open(test_drawing_path, 'w', encoding='utf-8') as f:
    f.write(content)

# 2. Fix test_guesses.py NameError and use _receive_until_type
test_guesses_path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_guesses.py'
with open(test_guesses_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Look for the NameError
# I suspect I replaced 'viewer_socket = ...' with something?
# No, I used .replace() on 'await viewer_socket.connect()'

# Let's just fix the whole test_guess_broadcast_visibility block
v_join_pattern = r'viewer_socket = WebsocketCommunicator\(.*?await viewer_socket\.connect\(\)'
v_join_replacement = 'viewer_socket = WebsocketCommunicator(\n            _TEST_APP,\n            _ws_url(self.room.join_code),\n            headers=_session_headers(self.viewer_key),\n        )\n        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)\n        _drain_output_queue_nowait(guesser_socket)'

content = re.sub(v_join_pattern, v_join_replacement, content, flags=re.DOTALL)

with open(test_guesses_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Final cleanup applied.")
