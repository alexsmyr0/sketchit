import os
path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_guesses.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if 'async def test_near_match_outcome_broadcast(self):' in line:
        new_lines.append(line)
        new_lines.append('        self._seed_active_round_state()\n')
    elif 'async def test_duplicate_outcome_broadcast(self):' in line:
        new_lines.append(line)
        new_lines.append('        self._seed_active_round_state()\n')
    elif '\\n' in line:
        # Fix the literal \n characters I just introduced
        new_lines.append(line.replace('\\n', '\n'))
    else:
        new_lines.append(line)

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Fixed test_guesses.py with proper newlines')
