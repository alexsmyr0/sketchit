"""Project-wide websocket routing."""

from django.urls import re_path

from rooms.consumers import RoomConsumer

# Join codes are exactly 8 uppercase alphanumeric characters, but we accept
# lowercase here too so the consumer can normalise them itself.
websocket_urlpatterns = [
    re_path(
        r"^ws/rooms/(?P<join_code>[A-Za-z0-9]{8})/$",
        RoomConsumer.as_asgi(),
    ),
]
