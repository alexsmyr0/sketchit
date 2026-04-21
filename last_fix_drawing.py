import os
import re

# 1. Fix test_drawing.py
path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix NameError in test_snapshot_isolation_between_rounds
# We need to add the communicator definition back
bad_block = """        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)
        
        # Viewer should receive NOTHING (no snapshot from old round) because the service cleared it
        self.assertTrue(await viewer_socket.receive_nothing())

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()"""

good_block = """        new_viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        await _connect_and_drain_initial_sync(new_viewer_socket, self.room.join_code, expects_game_active=True)
        
        # Viewer should receive NOTHING drawing-related (no snapshot from old round)
        drawing_msgs = [m for m in _drain_output_queue_nowait(new_viewer_socket) if m.get("type", "").startswith("drawing.")]
        self.assertEqual(drawing_msgs, [])
        self.assertTrue(await new_viewer_socket.receive_nothing())

        await drawer_socket.disconnect()
        await new_viewer_socket.disconnect()"""

content = content.replace(bad_block, good_block)

# Fix test_multiple_viewers_receive_broadcast to use _receive_until_type
content = content.replace(
    'resp1 = await viewer1_socket.receive_json_from()\n        resp2 = await viewer2_socket.receive_json_from()',
    'resp1 = await _receive_until_type(viewer1_socket, "drawing.stroke")\n        resp2 = await _receive_until_type(viewer2_socket, "drawing.stroke")'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("test_drawing.py final fix applied.")
