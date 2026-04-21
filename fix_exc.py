import re
with open('rooms/consumers.py', 'r') as f:
    c = f.read()
c = c.replace('except Exception as e:', 'except BaseException as e:\n            import traceback\n            print("CAUGHT EXCEPTION IN CONSUMER:")\n            traceback.print_exc()')
with open('rooms/consumers.py', 'w') as f:
    f.write(c)
