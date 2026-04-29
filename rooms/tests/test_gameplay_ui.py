"""Tests for browser-facing gameplay events over the room WebSocket."""

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
from channels.testing import WebsocketCommunicator
from channels.db import database_sync_to_async
from config.routing import websocket_urlpatterns
from channels.auth import AuthMiddlewareStack
from channels.routing import URLRouter
from rooms.models import Room, Player
from words.models import Word, WordPack, WordPackEntry
from django.contrib.sessions.backends.db import SessionStore
from games import runtime as game_runtime
from rooms.tests.test_consumers import (
    _connect_and_drain_initial_sync,
    _receive_until_type,
)

_TEST_APP = AuthMiddlewareStack(URLRouter(websocket_urlpatterns))


class GameplayUITests(TransactionTestCase):
    """Gameplay WebSocket UI tests that need committed rows across async tasks.

    TransactionTestCase is intentional here: Channels and database_sync_to_async
    may use different database connections, so the setup data must be committed
    and visible outside the main test method's connection.
    """

    def setUp(self):
        from rooms import consumers as room_consumers
        game_runtime.reset_runtime_state_for_tests()
        
        # The app normally talks to Redis for runtime room/game state. These
        # tests replace that dependency with fakeredis so they can focus on the
        # WebSocket behavior without requiring a real Redis server.
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
        """Restore patched runtime dependencies so one test cannot affect another."""
        from games import runtime as game_runtime
        game_runtime.reset_runtime_state_for_tests()
        from games import services as game_services
        game_services._get_redis_client = self._orig_services_redis

    def _session_headers(self):
        """Send the saved Django session key as a WebSocket cookie."""
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
        # Django's ORM is synchronous. Wrapping setup in database_sync_to_async
        # keeps the async test method from blocking the event loop.
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
            started_game = start_game_for_room(self.room)

            # The first drawer is random. Return the session key for whichever
            # player is not drawing so the test always submits a legal guess.
            if started_game.first_round.drawer_participant_id == self.player.id:
                return session2.session_key
            return self.session.session_key

        guesser_session_key = await _setup_second_player_and_start_game()

        communicator = WebsocketCommunicator(
            _TEST_APP,
            f"/ws/rooms/{self.room.join_code}/",
            headers=[(b"cookie", f"sessionid={guesser_session_key}".encode())]
        )
        await _connect_and_drain_initial_sync(
            communicator,
            self.room.join_code,
            expects_game_active=True,
        )

        # After connection, the consumer has already sent room and round sync
        # events. The actual behavior under test starts with this guess event.
        await communicator.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "apple"}
        })

        response = await _receive_until_type(communicator, "guess.result")
        self.assertEqual(response["type"], "guess.result")
        self.assertEqual(response["payload"]["outcome"], "correct")
        
        await communicator.disconnect()
