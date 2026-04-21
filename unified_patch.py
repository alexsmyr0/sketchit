import os
import re

def patch_file(path, replacements):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    for target, replacement in replacements:
        if target not in content:
            print(f"WARNING: Target not found in {os.path.basename(path)}:\n{target[:100]}...")
            # Try with different line endings or whitespace?
            # No, let's just fail and see.
        content = content.replace(target, replacement)
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Patched {os.path.basename(path)}")

# 1. test_drawing.py
patch_file(r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py', [
    # Handshake replacement
    ('await drawer_socket.connect()', 'await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=True)'),
    ('await viewer_socket.connect()', 'await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)'),
    ('await viewer1_socket.connect()', 'await _connect_and_drain_initial_sync(viewer1_socket, self.room.join_code, expects_game_active=True)'),
    ('await viewer2_socket.connect()', 'await _connect_and_drain_initial_sync(viewer2_socket, self.room.join_code, expects_game_active=True)'),
    ('await communicator.connect()', 'await _connect_and_drain_initial_sync(communicator, self.room.join_code, expects_game_active=True)'),
    
    # Assertions
    ('response = await viewer_socket.receive_json_from()\n        self.assertEqual(response["type"], "drawing.stroke")', 'response = await _receive_until_type(viewer_socket, "drawing.stroke")'),
    ('response = await viewer_socket.receive_json_from()\n        self.assertEqual(response["type"], "drawing.end_stroke")', 'response = await _receive_until_type(viewer_socket, "drawing.end_stroke")'),
    ('resp1 = await viewer1_socket.receive_json_from()\n        resp2 = await viewer2_socket.receive_json_from()', 'resp1 = await _receive_until_type(viewer1_socket, "drawing.stroke")\n        resp2 = await _receive_until_type(viewer2_socket, "drawing.stroke")'),
    ('response = await viewer_socket.receive_json_from()\n        self.assertEqual(response["type"], "drawing.clear")', 'response = await _receive_until_type(viewer_socket, "drawing.clear")'),
    
    # Safe drains
    ('self.assertTrue(await drawer_socket.receive_nothing())', '_drain_output_queue_nowait(drawer_socket)\n        self.assertTrue(await drawer_socket.receive_nothing())'),
    
    # Snapshot accumulation
    ('await sync_viewer.receive_json_from()', 'await _receive_until_type(sync_viewer, s["type"])'),
    ('response = await viewer_socket.receive_json_from()', 'response = await _receive_until_type(viewer_socket, expected["type"])'),
    
    # Sequence numbers
    ('sequence_number=1', 'sequence_number=2'), # We need to be careful with this one
])

# 2. test_guesses.py
patch_file(r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_guesses.py', [
    ('await guesser_socket.connect()', 'await _connect_and_drain_initial_sync(guesser_socket, self.room.join_code, expects_game_active=True)'),
    ('await drawer_socket.connect()', 'await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=True)'),
    ('await viewer_socket.connect()', 'await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)'),
    ('sequence_number=1', 'sequence_number=2'),
])

print("Final patch script executed.")
