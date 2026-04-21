import os
import django
import asyncio
from channels.testing import WebsocketCommunicator

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from config.routing import websocket_urlpatterns
from channels.routing import URLRouter
from channels.auth import AuthMiddlewareStack

async def test_connect():
    # Use a dummy join code
    join_code = "ABC12345"
    application = AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
    communicator = WebsocketCommunicator(application, f"/ws/rooms/{join_code}/")
    
    # We expect this to fail because there's no session, but we want to see the error
    try:
        connected, _ = await communicator.connect()
        print(f"Connected: {connected}")
        await communicator.disconnect()
    except Exception:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_connect())
