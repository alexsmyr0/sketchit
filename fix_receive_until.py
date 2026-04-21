import os
import re

path = r'c:\Users\chris\Documents\sketchit\sketchit\rooms\tests\test_consumers.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace _receive_until_type with a safe version
new_receive_until = """async def _receive_until_type(communicator, event_type: str, attempts: int = 20):
    \"\"\"Wait for and return a specific JSON event type, ignoring others (safe version).\"\"\"
    import asyncio, json
    for _ in range(attempts):
        try:
            # We use wait_for on the queue directly to avoid CancelledError on the app task
            raw = await asyncio.wait_for(communicator.output_queue.get(), timeout=1.0)
            if raw.get("type") == "websocket.send":
                event = json.loads(raw["text"])
                if event.get("type") == event_type:
                    return event
        except asyncio.TimeoutError:
            continue
    raise AssertionError(f"Did not receive expected event type '{event_type}'.")
"""

content = re.sub(r'async def _receive_until_type\(.*?\):.*?raise AssertionError\(.*?\)', new_receive_until, content, flags=re.DOTALL)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("test_consumers.py _receive_until_type fixed.")
