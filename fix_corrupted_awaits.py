import os
import re

for path in [
    r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py',
    r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_guesses.py'
]:
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Fix the corrupted await calls
    content = content.replace('await _connect_and_drain_initial_sync, _drain_output_queue_nowait', 'await _connect_and_drain_initial_sync')
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

print("Corrupted awaits fixed.")
