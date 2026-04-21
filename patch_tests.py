import os

def patch_file(path, search, replace, count=1):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    new_content = content.replace(search, replace, count)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)

# Patch test_drawing.py
# We want to replace the second occurrence of sequence_number=1 with sequence_number=2
# Or just replace the whole block in test_snapshot_isolation_between_rounds
test_drawing_path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_drawing.py'
with open(test_drawing_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

found_count = 0
for i, line in enumerate(lines):
    if 'sequence_number=1' in line:
        found_count += 1
        if found_count == 2:
            lines[i] = line.replace('sequence_number=1', 'sequence_number=2')
            break

with open(test_drawing_path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

# Patch test_guesses.py
test_guesses_path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_guesses.py'
with open(test_guesses_path, 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace(
    '        await guesser_socket.connect()',
    '        await _connect_and_drain_initial_sync(guesser_socket, self.room.join_code)'
)

with open(test_guesses_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched successfully.")
