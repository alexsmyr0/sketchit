from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import fakeredis
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from games import services as game_services
from rooms.models import Player, Room
from rooms.tests.test_consumers import (
    _ws_url, _session_headers, _create_room_member, _TEST_APP,
    _receive_until_type, _connect_and_receive_initial_room_state,
    _connect_and_drain_initial_sync, _drain_output_queue_nowait
)
from rooms import consumers as room_consumers
from games import redis as game_redis
from games.models import Game, GameStatus, GameWord, Guess, Round
from words.models import Word, WordPack, WordPackEntry


@database_sync_to_async
def _get_guess(round_id: int, player_id: int) -> Guess:
    return Guess.objects.get(round_id=round_id, player_id=player_id)


@database_sync_to_async
def _set_round_started_at(round_id: int, started_at) -> None:
    round = Round.objects.get(pk=round_id)
    round.started_at = started_at
    round.save(update_fields=("started_at", "updated_at"))


@database_sync_to_async
def _set_round_target_word(round_id: int, target_word: str) -> None:
    round = Round.objects.select_related("selected_game_word").get(pk=round_id)
    round.selected_game_word.text = target_word
    round.selected_game_word.save(update_fields=("text", "updated_at"))


async def _receive_until_type(
    communicator: WebsocketCommunicator,
    expected_type: str,
    *,
    max_messages: int = 8,
) -> dict:
    # Room sockets emit a direct room.state (and may emit runtime sync frames)
    # immediately after connect, so guess tests should drain until the event
    # under assertion appears.
    for _ in range(max_messages):
        message = await communicator.receive_json_from()
        if message.get("type") == expected_type:
            return message
    raise AssertionError(f"Did not receive {expected_type!r} within {max_messages} messages.")


@override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True)
class GuessPipelineTests(TransactionTestCase):
    def setUp(self):
        from games import runtime as game_runtime
        game_runtime.reset_runtime_state_for_tests()
        room_consumers.reset_redis_client()
        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis
        game_runtime._redis_client = self.fake_redis
        from games import services as game_services
        game_services._get_redis_client = lambda: self.fake_redis
        from rooms import services as room_services
        room_services._get_redis_client = lambda: self.fake_redis

        # Set up a word pack for the room
        self.word_pack = WordPack.objects.create(name="Test Pack")
        self.secret_word_text = "rocket"
        test_word = Word.objects.create(text=self.secret_word_text)
        spare_word = Word.objects.create(text="planet")
        WordPackEntry.objects.create(word_pack=self.word_pack, word=test_word)
        WordPackEntry.objects.create(word_pack=self.word_pack, word=spare_word)

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

        pass
        
        # Set up Game and Round
        self.game = Game.objects.create(room=self.room, status=GameStatus.IN_PROGRESS)
        self.game_word = GameWord.objects.create(game=self.game, text=self.secret_word_text)
        self.spare_game_word = GameWord.objects.create(game=self.game, text="planet")
        self.round = Round.objects.create(
            game=self.game,
            drawer_participant=self.drawer_player,
            drawer_nickname=self.drawer_player.display_name,
            selected_game_word=self.game_word,
            sequence_number=100,
        )

        # Tests that need an active round should call _seed_active_round_state.
        pass

    def _seed_active_round_state(self):
        from games import redis as game_redis
        from django.utils import timezone
        from datetime import timedelta
        state = {
            "phase": "round",
            "round_id": str(self.round.id),
            "game_id": str(self.game.id),
            "drawer_participant_id": str(self.drawer_player.id),
            "deadline_at": (timezone.now() + timedelta(seconds=60)).isoformat(),
        }
        game_redis.set_turn_state(self.fake_redis, self.room.join_code, state)

    def tearDown(self):
        room_consumers._redis_client = None

    async def test_correct_guess_broadcast(self):
        self._seed_active_round_state()
        round_start = timezone.now()
        await _set_round_started_at(self.round.id, round_start)
        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.guesser_key),
        )
        await _connect_and_drain_initial_sync(guesser_socket, self.room.join_code, expects_game_active=True)

        # Submit correct guess
        accepted_at = round_start + timedelta(seconds=45)
        import asyncio
        await asyncio.sleep(0.1)
        with patch("games.services.timezone.now", return_value=accepted_at):
            await guesser_socket.send_json_to({
                "type": "guess.submit",
                "payload": {"text": self.secret_word_text}
            })

            # Guesser should receive the result broadcast
            response = await _receive_until_type(guesser_socket, "guess.result")
        self.assertEqual(response["type"], "guess.result")
        self.assertTrue(response["payload"]["is_correct"])
        self.assertEqual(response["payload"]["outcome"], game_services.GuessOutcome.CORRECT)
        self.assertEqual(response["payload"]["player_id"], self.guesser_player.id)
        
        # Check exact time-based score updates
        score_updates = response["payload"]["score_updates"]
        guesser_update = next(s for s in score_updates if s["player_id"] == self.guesser_player.id)
        drawer_update = next(s for s in score_updates if s["player_id"] == self.drawer_player.id)
        self.assertEqual(guesser_update["current_score"], 33)
        self.assertEqual(drawer_update["current_score"], 17)

        await guesser_socket.disconnect()

    async def test_valid_guess_is_persisted_on_active_round(self):
        self._seed_active_round_state()
        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.guesser_key),
        )
        await _connect_and_drain_initial_sync(guesser_socket, self.room.join_code, expects_game_active=True)

        await guesser_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": f"  {self.secret_word_text}  "}
        })

        response = await _receive_until_type(guesser_socket, "guess.result")
        self.assertEqual(response["type"], "guess.result")

        persisted_guess = await _get_guess(self.round.id, self.guesser_player.id)
        self.assertEqual(persisted_guess.round_id, self.round.id)
        self.assertEqual(persisted_guess.player_id, self.guesser_player.id)
        self.assertEqual(persisted_guess.text, self.secret_word_text)
        self.assertTrue(persisted_guess.is_correct)

        await guesser_socket.disconnect()

    async def test_incorrect_guess_broadcast(self):
        self._seed_active_round_state()
        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.guesser_key),
        )
        await _connect_and_drain_initial_sync(guesser_socket, self.room.join_code, expects_game_active=True)

        # Submit incorrect guess
        await guesser_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "wrongword"}
        })

        # Guesser should receive a failure result broadcast
        response = await _receive_until_type(guesser_socket, "guess.result")
        self.assertEqual(response["type"], "guess.result")
        self.assertFalse(response["payload"]["is_correct"])
        self.assertEqual(response["payload"]["outcome"], game_services.GuessOutcome.INCORRECT)
        self.assertEqual(response["payload"]["text"], "wrongword")

        await guesser_socket.disconnect()

    async def test_near_match_outcome_broadcast(self):
        self._seed_active_round_state()
        await _set_round_target_word(self.round.id, "new york city")
        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.guesser_key),
        )
        await _connect_and_drain_initial_sync(guesser_socket, self.room.join_code, expects_game_active=True)

        await guesser_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "york"}
        })

        response = await _receive_until_type(guesser_socket, "guess.result")
        self.assertEqual(response["type"], "guess.result")
        self.assertFalse(response["payload"]["is_correct"])
        self.assertEqual(response["payload"]["outcome"], game_services.GuessOutcome.NEAR_MATCH)

        await guesser_socket.disconnect()

    async def test_duplicate_outcome_broadcast(self):
        self._seed_active_round_state()
        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.guesser_key),
        )
        await _connect_and_drain_initial_sync(guesser_socket, self.room.join_code, expects_game_active=True)

        await guesser_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "planet"}
        })
        first_response = await _receive_until_type(guesser_socket, "guess.result")

        await guesser_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "  PLANET "}
        })
        second_response = await _receive_until_type(guesser_socket, "guess.result")

        self.assertEqual(first_response["payload"]["outcome"], game_services.GuessOutcome.INCORRECT)
        self.assertEqual(second_response["payload"]["outcome"], game_services.GuessOutcome.DUPLICATE)
        self.assertFalse(second_response["payload"]["is_correct"])

        await guesser_socket.disconnect()

    async def test_guess_submission_delegates_to_game_service(self):
        self._seed_active_round_state()
        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.guesser_key),
        )
        await _connect_and_drain_initial_sync(guesser_socket, self.room.join_code, expects_game_active=True)

        stub_result = SimpleNamespace(
            outcome=game_services.GuessOutcome.INCORRECT,
            is_correct=False,
            round_completed=False,
            score_updates=(),
        )
        with patch(
            "rooms.consumers.game_services.evaluate_guess_for_round",
            return_value=stub_result,
        ) as mocked_evaluate_guess:
            await guesser_socket.send_json_to({
                "type": "guess.submit",
                "payload": {"text": "  trimmed guess  "}
            })

            response = await _receive_until_type(guesser_socket, "guess.result")

        self.assertEqual(response["type"], "guess.result")
        called_round, called_player, called_text = mocked_evaluate_guess.call_args.args
        self.assertEqual(called_round.id, self.round.id)
        self.assertEqual(called_player.id, self.guesser_player.id)
        self.assertEqual(called_text, "trimmed guess")

        await guesser_socket.disconnect()

    async def test_drawer_cannot_guess(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_key),
        )
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=True)

        # Drawer tries to guess their own word
        await drawer_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": self.secret_word_text}
        })

        # Drawer should receive a guess.error (rejected by consumer before hitting service/DB)
        response = await _receive_until_type(drawer_socket, "guess.error")
        self.assertEqual(response["type"], "guess.error")
        self.assertIn("Drawers cannot submit guesses", response["payload"]["message"])

        await drawer_socket.disconnect()

    async def test_service_validation_error_becomes_guess_error(self):
        self._seed_active_round_state()
        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.guesser_key),
        )
        await _connect_and_drain_initial_sync(guesser_socket, self.room.join_code, expects_game_active=True)

        with patch(
            "rooms.consumers.game_services.evaluate_guess_for_round",
            side_effect=game_services.GuessEvaluationError("Service rejected guess."),
        ):
            await guesser_socket.send_json_to({
                "type": "guess.submit",
                "payload": {"text": "guess"}
            })

            response = await _receive_until_type(guesser_socket, "guess.error")

        self.assertEqual(response["type"], "guess.error")
        self.assertEqual(response["payload"]["message"], "Service rejected guess.")

        await guesser_socket.disconnect()

    async def test_guess_broadcast_visibility(self):
        self._seed_active_round_state()
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
        
        await _connect_and_drain_initial_sync(guesser_socket, self.room.join_code, expects_game_active=True)
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)

        # Guesser submits a guess
        await guesser_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "something"}
        })

        # Both should receive the broadcast
        resp_g = await _receive_until_type(guesser_socket, "guess.result")
        resp_v = await _receive_until_type(viewer_socket, "guess.result")
        
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
        await _connect_and_drain_initial_sync(guesser_socket, self.room.join_code, expects_game_active=False)

        await guesser_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "something"}
        })

        # Should receive a guess.error instead of silent return
        response = await _receive_until_type(guesser_socket, "guess.error")
        self.assertEqual(response["type"], "guess.error")
        self.assertEqual(response["payload"]["message"], "No active round in progress.")

        await guesser_socket.disconnect()

    async def test_guess_empty_text(self):
        self._seed_active_round_state()
        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.guesser_key),
        )
        await _connect_and_drain_initial_sync(guesser_socket, self.room.join_code, expects_game_active=True)

        # Submit whitespace-only guess
        await guesser_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "   "}
        })

        # Should receive a guess.error instead of silent return
        response = await _receive_until_type(guesser_socket, "guess.error")
        self.assertEqual(response["type"], "guess.error")
        self.assertEqual(response["payload"]["message"], "Guess text cannot be empty.")

        await guesser_socket.disconnect()
