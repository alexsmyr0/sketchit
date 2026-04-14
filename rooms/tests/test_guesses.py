import fakeredis
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase

from rooms.models import Player, Room
from rooms.tests.test_consumers import _ws_url, _session_headers, _create_room_member, _TEST_APP
from rooms import consumers as room_consumers
from games import redis as game_redis
from games.models import Game, GameStatus, GameWord, Round, Word
from words.models import WordPack, WordPackEntry

class GuessPipelineTests(TransactionTestCase):
    def setUp(self):
        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis

        # Set up a word pack for the room
        self.word_pack = WordPack.objects.create(name="Test Pack")
        self.secret_word_text = "rocket"
        test_word = Word.objects.create(text=self.secret_word_text)
        WordPackEntry.objects.create(word_pack=self.word_pack, word=test_word)

        self.room = Room.objects.create(
            name="Guess Room",
            join_code="GUESS123",
            status=Room.Status.IN_PROGRESS,
            word_pack=self.word_pack,
        )
        
        # Create drawer and guesser
        self.drawer_key = async_to_sync(_create_room_member)(self.room.id, "Drawer")
        self.drawer_player = Player.objects.get(room=self.room, session_key=self.drawer_key)
        
        self.guesser_key = async_to_sync(_create_room_member)(self.room.id, "Guesser")
        self.guesser_player = Player.objects.get(room=self.room, session_key=self.guesser_key)

        # Set up Game and Round
        self.game = Game.objects.create(room=self.room, status=GameStatus.IN_PROGRESS)
        self.game_word = GameWord.objects.create(game=self.game, text=self.secret_word_text)
        self.round = Round.objects.create(
            game=self.game,
            drawer_participant=self.drawer_player,
            drawer_nickname=self.drawer_player.display_name,
            selected_game_word=self.game_word,
            sequence_number=1,
        )

        # Set the active round in Redis turn state
        game_redis.set_turn_state(self.fake_redis, self.room.join_code, {
            "drawer_participant_id": self.drawer_player.id,
            "round_id": self.round.id,
            "game_id": self.game.id,
            "phase": "round"
        })

    def tearDown(self):
        room_consumers._redis_client = None

    async def test_correct_guess_broadcast(self):
        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.guesser_key),
        )
        await guesser_socket.connect()

        # Submit correct guess
        await guesser_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": self.secret_word_text}
        })

        # Guesser should receive the result broadcast
        response = await guesser_socket.receive_json_from()
        self.assertEqual(response["type"], "guess.result")
        self.assertTrue(response["payload"]["is_correct"])
        self.assertEqual(response["payload"]["player_id"], self.guesser_player.id)
        
        # Check score updates (guesser gets 1 point)
        score_updates = response["payload"]["score_updates"]
        guesser_update = next(s for s in score_updates if s["player_id"] == self.guesser_player.id)
        self.assertEqual(guesser_update["current_score"], 1)

        await guesser_socket.disconnect()

    async def test_incorrect_guess_broadcast(self):
        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.guesser_key),
        )
        await guesser_socket.connect()

        # Submit incorrect guess
        await guesser_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "wrongword"}
        })

        # Guesser should receive a failure result broadcast
        response = await guesser_socket.receive_json_from()
        self.assertEqual(response["type"], "guess.result")
        self.assertFalse(response["payload"]["is_correct"])
        self.assertEqual(response["payload"]["text"], "wrongword")

        await guesser_socket.disconnect()

    async def test_drawer_cannot_guess(self):
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_key),
        )
        await drawer_socket.connect()

        # Drawer tries to guess their own word
        await drawer_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": self.secret_word_text}
        })

        # Drawer should receive a guess.error (rejected by consumer before hitting service/DB)
        response = await drawer_socket.receive_json_from()
        self.assertEqual(response["type"], "guess.error")
        self.assertIn("Drawers cannot submit guesses", response["payload"]["message"])

        await drawer_socket.disconnect()

    async def test_guess_broadcast_visibility(self):
        # Third player joins as a viewer
        viewer_key = await _create_room_member(self.room.id, "Viewer")
        
        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.guesser_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(viewer_key),
        )
        
        await guesser_socket.connect()
        await viewer_socket.connect()

        # Guesser submits a guess
        await guesser_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "something"}
        })

        # Both should receive the broadcast
        resp_g = await guesser_socket.receive_json_from()
        resp_v = await viewer_socket.receive_json_from()
        
        self.assertEqual(resp_g["type"], "guess.result")
        self.assertEqual(resp_v["type"], "guess.result")
        self.assertEqual(resp_g["payload"]["text"], "something")
        self.assertEqual(resp_v["payload"]["text"], "something")

        await guesser_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_guess_no_active_round(self):
        # Clear round_id from Redis
        game_redis.clear_turn_state(self.fake_redis, self.room.join_code)

        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.guesser_key),
        )
        await guesser_socket.connect()

        await guesser_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "something"}
        })

        # Should receive a guess.error instead of silent return
        response = await guesser_socket.receive_json_from()
        self.assertEqual(response["type"], "guess.error")
        self.assertEqual(response["payload"]["message"], "No active round in progress.")

        await guesser_socket.disconnect()

    async def test_guess_empty_text(self):
        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.guesser_key),
        )
        await guesser_socket.connect()

        # Submit whitespace-only guess
        await guesser_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "   "}
        })

        # Should receive a guess.error instead of silent return
        response = await guesser_socket.receive_json_from()
        self.assertEqual(response["type"], "guess.error")
        self.assertEqual(response["payload"]["message"], "Guess text cannot be empty.")

        await guesser_socket.disconnect()
