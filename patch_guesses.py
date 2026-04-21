import os
import re

path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_guesses.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update imports
content = content.replace('_connect_and_drain_initial_sync', '_connect_and_drain_initial_sync, _drain_output_queue_nowait')

# 2. Add missing seeds
for method in ['test_near_match_outcome_broadcast', 'test_duplicate_outcome_broadcast']:
    pattern = rf'async def {method}\(self\):'
    content = re.sub(pattern, f'async def {method}(self):\n        self._seed_active_round_state()', content)

# 3. Use _connect_and_drain_initial_sync
content = content.replace('await guesser_socket.connect()', 'await _connect_and_drain_initial_sync(guesser_socket, self.room.join_code, expects_game_active=True)')
content = content.replace('await viewer_socket.connect()', 'await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)')

# 4. Fix test_guess_broadcast_visibility to drain guesser after viewer joins
content = content.replace(
    'await viewer_socket.connect()',
    'await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)\n        _drain_output_queue_nowait(guesser_socket)'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("test_guesses.py patched.")
