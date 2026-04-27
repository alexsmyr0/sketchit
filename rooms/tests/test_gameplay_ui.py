import json
import asyncio
import os
import fakeredis
from datetime import timedelta
from django.utils import timezone

# Set dummy env vars before anything imports django settings
os.environ.setdefault("MYSQL_DATABASE", "test")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from channels.testing import WebsocketCommunicator
from channels.db import database_sync_to_async
from config.routing import websocket_urlpatterns
from channels.auth import AuthMiddlewareStack
from channels.routing import URLRouter
from rooms.models import Room, Player
from words.models import Word, WordPack, WordPackEntry
from django.contrib.sessions.backends.db import SessionStore
from games.services import start_game_for_room
from games import runtime as game_runtime

_TEST_APP = AuthMiddlewareStack(URLRouter(websocket_urlpatterns))

@override_settings(DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}})
class GameplayUITests(TransactionTestCase):
    def setUp(self):
        from rooms import consumers as room_consumers
        game_runtime.reset_runtime_state_for_tests()
        
        # Patch Redis
        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis
        game_runtime._redis_client = self.fake_redis
        from games import services as game_services
        self._orig_services_redis = game_services._get_redis_client
        game_services._get_redis_client = lambda: self.fake_redis

        self.pack = WordPack.objects.create(name="Test Pack")
        self.word = Word.objects.create(text="apple")
        WordPackEntry.objects.create(word_pack=self.pack, word=self.word)
        
        self.room = Room.objects.create(
            name="Test Room",
            join_code="TEST1234",
            word_pack=self.pack
        )
        
        self.session = SessionStore()
        self.session.save()
        self.player = Player.objects.create(
            room=self.room,
            session_key=self.session.session_key,
            display_name="Alice",
            connection_status=Player.ConnectionStatus.CONNECTED,
            session_expires_at=timezone.now() + timedelta(hours=1)
        )
        self.room.host = self.player
        self.room.save()

    def tearDown(self):
        from games import runtime as game_runtime
        game_runtime.reset_runtime_state_for_tests()
        from games import services as game_services
        game_services._get_redis_client = self._orig_services_redis

    def _session_headers(self):
        return [(b"cookie", f"sessionid={self.session.session_key}".encode())]

    @override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True)
    async def test_room_state_includes_current_player_id(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            f"/ws/rooms/{self.room.join_code}/",
            headers=self._session_headers()
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        
        response = await communicator.receive_json_from()
        self.assertEqual(response["type"], "room.state")
        self.assertEqual(response["payload"]["current_player_id"], self.player.id)
        
        await communicator.disconnect()

    @override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True)
    async def test_guess_submission_and_result(self):
        # Add another player to start the game
        @database_sync_to_async
        def _setup_second_player_and_start_game():
            session2 = SessionStore()
            session2.save()
            Player.objects.create(
                room=self.room,
                session_key=session2.session_key,
                display_name="Bob",
                connection_status=Player.ConnectionStatus.CONNECTED,
                session_expires_at=timezone.now() + timedelta(hours=1)
            )
            from games.services import start_game_for_room
            start_game_for_room(self.room)

        await _setup_second_player_and_start_game()
        
        communicator = WebsocketCommunicator(
            _TEST_APP,
            f"/ws/rooms/{self.room.join_code}/",
            headers=self._session_headers()
        )
        await communicator.connect()
        
        # Drain initial sync events
        while True:
            try:
                msg = await communicator.receive_json_from(timeout=0.1)
            except asyncio.TimeoutError:
                break
        
        # Submit a guess
        await communicator.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "apple"}
        })
        
        response = await communicator.receive_json_from()
        self.assertEqual(response["type"], "guess.result")
        self.assertEqual(response["payload"]["outcome"], "correct")
        
        await communicator.disconnect()
