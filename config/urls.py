"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path, register_converter

from rooms.url_converters import JoinCodeConverter
from rooms.views import (
    create_room,
    join_room,
    leave_room,
    public_room_directory,
    room_entry_page,
    room_lobby_state,
    start_game,
    update_lobby_settings,
)

# Path-level shape check for join codes so any view that takes one is
# guaranteed to see a normalised 8-char alphanumeric value, never raw
# user input that could be unsafe to pass to the ORM or to Redis keys.
register_converter(JoinCodeConverter, "join_code")

urlpatterns = [
    path("", room_entry_page, name="room-entry"),
    path("rooms/create/", create_room, name="create-room"),
    path("rooms/public/", public_room_directory, name="public-room-directory"),
    path("rooms/<join_code:join_code>/join/", join_room, name="join-room"),
    path("rooms/<join_code:join_code>/", room_lobby_state, name="room-lobby-state"),
    path("rooms/<join_code:join_code>/settings/", update_lobby_settings, name="update-lobby-settings"),
    path("rooms/<join_code:join_code>/start-game/", start_game, name="start-game"),
    path("rooms/<join_code:join_code>/leave/", leave_room, name="leave-room"),
]
