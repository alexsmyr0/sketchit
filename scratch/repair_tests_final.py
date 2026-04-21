import os
import re

def repair_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Expand Redis Isolation in setUp
    setup_patch = """        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis
        from games import services as game_services
        from games import runtime as game_runtime
        self._orig_services_redis = game_services._get_redis_client
        self._orig_runtime_redis = game_runtime._redis_client
        game_services._get_redis_client = lambda: self.fake_redis
        game_runtime._redis_client = self.fake_redis
"""
    # Replace the old setup body with the patched one
    content = re.sub(
        r'def setUp\(self\):.*?room_consumers\._redis_client = self\.fake_redis\n',
        'def setUp(self):\n' + setup_patch,
        content,
        flags=re.DOTALL
    )

    # 2. Expand Restoration in tearDown
    teardown_patch = """    def tearDown(self):
        room_consumers._redis_client = None
        from games import services as game_services
        from games import runtime as game_runtime
        game_services._get_redis_client = self._orig_services_redis
        game_runtime._redis_client = self._orig_runtime_redis
        super().tearDown()
"""
    content = re.sub(
        r'def tearDown\(self\):.*?room_consumers\._redis_client = None\n',
        teardown_patch,
        content,
        flags=re.DOTALL
    )

    # 3. Inject deadline_at into all set_turn_state calls
    def deadline_repl(match):
        body = match.group(2)
        if "deadline_at" in body:
            return match.group(0)
        # Inject before the closing brace
        return f'game_redis.set_turn_state({match.group(1)}, {{{body}, "deadline_at": (timezone.now() + timedelta(seconds=60)).isoformat()}})'

    content = re.sub(
        r'game_redis\.set_turn_state\((.*?), \{(.*?)\}\)',
        deadline_repl,
        content,
        flags=re.DOTALL
    )

    # 4. Harden event consumption (receive_until_type)
    # Replace guest_socket.receive_json_from() with _receive_until_type(socket, type)
    # This is tricky because we need to know the type.
    # I'll target common ones in broadcasting tests.
    
    replacements = [
        # Guesses
        (r'response = await guesser_socket\.receive_json_from\(\)\n\s+self\.assertEqual\(response\["type"\], "guess\.result"\)',
         'response = await _receive_until_type(guesser_socket, "guess.result")\n        self.assertEqual(response["type"], "guess.result")'),
        (r'resp_g = await guesser_socket\.receive_json_from\(\)\n\s+resp_v = await viewer_socket\.receive_json_from\(\)',
         'resp_g = await _receive_until_type(guesser_socket, "guess.result")\n        resp_v = await _receive_until_type(viewer_socket, "guess.result")'),
         
        # Drawing
        (r'response = await viewer_socket\.receive_json_from\(\)\n\s+self\.assertEqual\(response\["type"\], "drawing\.stroke"\)',
         'response = await _receive_until_type(viewer_socket, "drawing.stroke")\n        self.assertEqual(response["type"], "drawing.stroke")'),
        (r'response = await viewer_socket\.receive_json_from\(\)\n\s+self\.assertEqual\(response\["type"\], "drawing\.end_stroke"\)',
         'response = await _receive_until_type(viewer_socket, "drawing.end_stroke")\n        self.assertEqual(response["type"], "drawing.end_stroke")'),
        (r'resp1 = await viewer1_socket\.receive_json_from\(\)\n\s+resp2 = await viewer2_socket\.receive_json_from\(\)',
         'resp1 = await _receive_until_type(viewer1_socket, "drawing.stroke")\n        resp2 = await _receive_until_type(viewer2_socket, "drawing.stroke")'),
    ]

    for pattern, repl in replacements:
        content = re.sub(pattern, repl, content)

    # 5. Remove legacy parameters (if any left)
    content = content.replace(', drain_duplicate_room_states=True', '')
    content = content.replace(', expected_total_frames=1', '')
    content = content.replace(', expected_total_frames=3', '')
    content = content.replace(', expected_total_frames=4', '')
    content = content.replace(', expected_sync_count=2', '')

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Repaired {path}")

repair_file('rooms/tests/test_drawing.py')
repair_file('rooms/tests/test_guesses.py')
