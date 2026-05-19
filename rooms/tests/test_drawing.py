"""
Tests for drawing event broadcasting and snapshot synchronization.

These tests verify that only the active drawer can broadcast drawing events
and that the server correctly manages the canvas snapshot in Redis for
reconnect recovery.
"""

import json
import fakeredis
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings

from asgiref.sync import async_to_sync
from rooms.models import Player, Room
from games.models import Game, GameStatus, GameWord, Round
from rooms.tests.test_consumers import (
    _ws_url, _session_headers, _create_room_member, _TEST_APP,
    _receive_until_type, _connect_and_drain_initial_sync, _drain_output_queue_safe,
)
from rooms import consumers as room_consumers
from rooms import redis as room_redis
from games import redis as game_redis
from games import services as game_services
from words.models import Word, WordPack, WordPackEntry


def _drawing_replay_events(messages: list[dict]) -> list[dict]:
    return [
        message
        for message in messages
        if message.get("type", "").startswith("drawing.")
    ]


def _assert_active_connect_messages(
    testcase: TransactionTestCase,
    messages: list[dict],
    *,
    replayed_drawing: list[dict] | None = None,
) -> None:
    testcase.assertEqual(messages[0].get("type"), "room.state")
    testcase.assertTrue(
        any(message.get("type") == "round.state" for message in messages),
        "Expected connect-time round.state sync event.",
    )
    testcase.assertTrue(
        any(
            message.get("type") in {"round.timer", "round.intermission_timer"}
            for message in messages
        ),
        "Expected connect-time round timer or intermission timer sync event.",
    )
    expected_replay = [] if replayed_drawing is None else replayed_drawing
    testcase.assertEqual(_drawing_replay_events(messages), expected_replay)


async def _connect_and_assert_active_handshake(
    testcase: TransactionTestCase,
    communicator: WebsocketCommunicator,
    join_code: str,
    *,
    replayed_drawing: list[dict] | None = None,
) -> list[dict]:
    messages = await _connect_and_drain_initial_sync(
        communicator,
        join_code,
        expects_game_active=True,
    )
    _assert_active_connect_messages(
        testcase,
        messages,
        replayed_drawing=replayed_drawing,
    )
    return messages


@override_settings(
    SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True,
    CHANNEL_LAYERS={
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    },
)
class DrawingEventTests(TransactionTestCase):
    """Tests for drawer authorization and event broadcasting.

    Forces InMemoryChannelLayer to sidestep the redis.asyncio cleanup
    cascade ("Future attached to a different loop") that flakes Redis-
    layer consumer tests under suite load — same approach as
    RoomConsumerConnectTests.
    """

    def setUp(self):
        from games import runtime as game_runtime
        game_runtime.reset_runtime_state_for_tests()
        room_consumers.reset_redis_client()

        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis
        game_runtime._redis_client = self.fake_redis

        self._orig_game_services_redis = game_services._get_redis_client
        game_services._get_redis_client = lambda: self.fake_redis

        # Set up a word pack for the room
        self.word_pack = WordPack.objects.create(name="Test Pack")
        test_word = Word.objects.create(text="rocket")
        WordPackEntry.objects.create(word_pack=self.word_pack, word=test_word)

        self.room = Room.objects.create(
            name="Drawing Room",
            join_code="DRAW1234",
            status=Room.Status.IN_PROGRESS,  # Active game needed for drawing
            word_pack=self.word_pack,
        )
        
        # Create participants with proper synchronization
        self.drawer_session_key = async_to_sync(_create_room_member)(self.room.id, "Drawer")
        self.drawer_player = Player.objects.get(room=self.room, session_key=self.drawer_session_key)
        
        self.viewer_session_key = async_to_sync(_create_room_member)(self.room.id, "Viewer")
        self.viewer_player = Player.objects.get(room=self.room, session_key=self.viewer_session_key)

        self.second_drawer_session_key = async_to_sync(_create_room_member)(
            self.room.id,
            "Second Drawer",
        )
        self.second_drawer_player = Player.objects.get(
            room=self.room,
            session_key=self.second_drawer_session_key,
        )

        self.spectator_session_key = async_to_sync(_create_room_member)(self.room.id, "Spectator")
        self.spectator_player = Player.objects.get(
            room=self.room,
            session_key=self.spectator_session_key,
        )
        self.spectator_player.participation_status = Player.ParticipationStatus.SPECTATING
        self.spectator_player.save(update_fields=("participation_status",))

        # Create mandatory Game and Round objects for runtime sync
        self.game = Game.objects.create(room=self.room, status=GameStatus.IN_PROGRESS)
        self.game_word = GameWord.objects.create(game=self.game, text="testword")
        self.round = Round.objects.create(
            game=self.game,
            drawer_participant=self.drawer_player,
            drawer_nickname=self.drawer_player.display_name,
            selected_game_word=self.game_word,
            sequence_number=1,
        )

    def _seed_active_round_state(self, *, duo: bool = False):
        from games import redis as game_redis
        from django.utils import timezone
        from datetime import timedelta
        state = {
            "phase": "round",
            "status": "drawing",
            "round_id": str(self.round.id),
            "game_id": str(self.game.id),
            "drawer_participant_id": str(self.drawer_player.id),
            "second_drawer_participant_id": (
                str(self.second_drawer_player.id) if duo else ""
            ),
            "deadline_at": (timezone.now() + timedelta(seconds=60)).isoformat(),
        }
        game_redis.set_turn_state(self.fake_redis, self.room.join_code, state)

    def tearDown(self):
        room_consumers.reset_redis_client()
        game_services._get_redis_client = self._orig_game_services_redis
        super().tearDown()

    async def test_drawer_can_broadcast_stroke(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        await _connect_and_assert_active_handshake(
            self,
            drawer_socket,
            self.room.join_code,
        )
        await _connect_and_assert_active_handshake(
            self,
            viewer_socket,
            self.room.join_code,
        )
        await _receive_until_type(drawer_socket, "room.state")

        stroke_data = {"lines": [[0,0], [10,10]], "color": "blue"}
        await drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": stroke_data
        })
        
        # Viewer should receive the broadcast
        response = await _receive_until_type(viewer_socket, "drawing.stroke")
        self.assertEqual(response["payload"], stroke_data)

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_viewer_receives_stroke(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        await _connect_and_assert_active_handshake(
            self,
            drawer_socket,
            self.room.join_code,
        )
        await _connect_and_assert_active_handshake(
            self,
            viewer_socket,
            self.room.join_code,
        )
        await _receive_until_type(drawer_socket, "room.state")

        stroke_data = {"lines": [[0,0], [10,10]], "color": "red"}
        await drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": stroke_data
        })

        # Viewer should receive the broadcast
        response = await _receive_until_type(viewer_socket, "drawing.stroke")
        self.assertEqual(response["payload"], stroke_data)

        # Drawer should NOT receive their own broadcast.
        self.assertTrue(await drawer_socket.receive_nothing())

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_drawing_end_stroke_broadcast(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        
        await _connect_and_assert_active_handshake(
            self,
            drawer_socket,
            self.room.join_code,
        )
        await _connect_and_assert_active_handshake(
            self,
            viewer_socket,
            self.room.join_code,
        )
        await _receive_until_type(drawer_socket, "room.state")
        # Drawer sends an end stroke
        await drawer_socket.send_json_to({
            "type": "drawing.end_stroke",
            "payload": {}
        })

        # Viewer should receive the broadcast
        response = await _receive_until_type(viewer_socket, "drawing.end_stroke")

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_multiple_viewers_receive_broadcast(self):
        self._seed_active_round_state()
        # Create a second viewer
        second_viewer_key = await _create_room_member(self.room.id, "Viewer 2")
        
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        viewer1_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        viewer2_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(second_viewer_key),
        )
        
        await _connect_and_assert_active_handshake(
            self,
            drawer_socket,
            self.room.join_code,
        )
        await _connect_and_assert_active_handshake(
            self,
            viewer1_socket,
            self.room.join_code,
        )
        await _connect_and_assert_active_handshake(
            self,
            viewer2_socket,
            self.room.join_code,
        )
        await _receive_until_type(drawer_socket, "room.state")
        await _receive_until_type(drawer_socket, "room.state")

        stroke_data = {"lines": [[0,0], [10,10]]}
        await drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": stroke_data
        })

        # Both viewers should receive the broadcast
        resp1 = await _receive_until_type(viewer1_socket, "drawing.stroke")
        resp2 = await _receive_until_type(viewer2_socket, "drawing.stroke")
        
        self.assertEqual(resp1["payload"], stroke_data)
        self.assertEqual(resp2["payload"], stroke_data)

        await drawer_socket.disconnect()
        await viewer1_socket.disconnect()
        await viewer2_socket.disconnect()

    async def test_non_drawer_cannot_broadcast_stroke(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        
        await _connect_and_assert_active_handshake(
            self,
            drawer_socket,
            self.room.join_code,
        )
        await _connect_and_assert_active_handshake(
            self,
            viewer_socket,
            self.room.join_code,
        )
        await _receive_until_type(drawer_socket, "room.state")

        # Viewer (non-drawer) tries to send a stroke
        await viewer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": {"naughty": "secret"}
        })

        # Drawer should receive nothing from the unauthorized viewer stroke.
        self.assertTrue(await drawer_socket.receive_nothing())

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_duo_round_accepts_drawing_from_either_drawer_on_shared_canvas(self):
        self._seed_active_round_state(duo=True)
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        second_drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.second_drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )

        await _connect_and_assert_active_handshake(self, drawer_socket, self.room.join_code)
        await _connect_and_assert_active_handshake(
            self,
            second_drawer_socket,
            self.room.join_code,
        )
        await _connect_and_assert_active_handshake(self, viewer_socket, self.room.join_code)
        await _receive_until_type(drawer_socket, "room.state")
        await _receive_until_type(drawer_socket, "room.state")

        primary_payload = {"lines": [[0, 0], [4, 4]], "color": "blue"}
        secondary_payload = {"lines": [[9, 1], [9, 8]], "color": "green"}

        await drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": primary_payload,
        })
        await _receive_until_type(second_drawer_socket, "drawing.stroke")
        await _receive_until_type(viewer_socket, "drawing.stroke")

        await second_drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": secondary_payload,
        })
        primary_received = await _receive_until_type(drawer_socket, "drawing.stroke")
        viewer_received = await _receive_until_type(viewer_socket, "drawing.stroke")

        self.assertEqual(primary_received["payload"], secondary_payload)
        self.assertEqual(viewer_received["payload"], secondary_payload)
        self.assertNotIn("drawer_participant_id", viewer_received["payload"])
        self.assertNotIn("second_drawer_participant_id", viewer_received["payload"])

        snapshot = [
            json.loads(item.decode())
            for item in room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code)
        ]
        self.assertEqual(
            snapshot,
            [
                {"type": "drawing.stroke", "payload": primary_payload},
                {"type": "drawing.stroke", "payload": secondary_payload},
            ],
        )

        late_viewer_key = await _create_room_member(self.room.id, "Late Viewer")
        late_viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(late_viewer_key),
        )
        await _connect_and_assert_active_handshake(
            self,
            late_viewer_socket,
            self.room.join_code,
            replayed_drawing=snapshot,
        )

        await drawer_socket.disconnect()
        await second_drawer_socket.disconnect()
        await viewer_socket.disconnect()
        await late_viewer_socket.disconnect()

    async def test_duo_round_rejects_non_drawers_and_spectators(self):
        self._seed_active_round_state(duo=True)
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        guesser_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        spectator_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.spectator_session_key),
        )

        await _connect_and_assert_active_handshake(self, drawer_socket, self.room.join_code)
        await _connect_and_assert_active_handshake(self, guesser_socket, self.room.join_code)
        await _connect_and_assert_active_handshake(self, spectator_socket, self.room.join_code)
        await _receive_until_type(drawer_socket, "room.state")
        await _receive_until_type(drawer_socket, "room.state")

        await guesser_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": {"from": "guesser"},
        })
        await spectator_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": {"from": "spectator"},
        })

        self.assertTrue(await drawer_socket.receive_nothing())
        self.assertEqual(
            room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code),
            [],
        )

        await drawer_socket.disconnect()
        await guesser_socket.disconnect()
        await spectator_socket.disconnect()

    async def test_duo_round_broadcasts_second_drawer_events_to_all_viewers(self):
        self._seed_active_round_state(duo=True)
        third_viewer_key = await _create_room_member(self.room.id, "Viewer 2")
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        second_drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.second_drawer_session_key),
        )
        viewer1_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        viewer2_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(third_viewer_key),
        )

        await _connect_and_assert_active_handshake(self, drawer_socket, self.room.join_code)
        await _connect_and_assert_active_handshake(
            self,
            second_drawer_socket,
            self.room.join_code,
        )
        await _connect_and_assert_active_handshake(self, viewer1_socket, self.room.join_code)
        await _connect_and_assert_active_handshake(self, viewer2_socket, self.room.join_code)
        await _receive_until_type(drawer_socket, "room.state")
        await _receive_until_type(drawer_socket, "room.state")
        await _receive_until_type(drawer_socket, "room.state")

        payload = {"lines": [[2, 3], [5, 8]], "brush": "marker"}
        await second_drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": payload,
        })

        primary_received = await _receive_until_type(drawer_socket, "drawing.stroke")
        viewer1_received = await _receive_until_type(viewer1_socket, "drawing.stroke")
        viewer2_received = await _receive_until_type(viewer2_socket, "drawing.stroke")

        self.assertEqual(primary_received["payload"], payload)
        self.assertEqual(viewer1_received["payload"], payload)
        self.assertEqual(viewer2_received["payload"], payload)

        await drawer_socket.disconnect()
        await second_drawer_socket.disconnect()
        await viewer1_socket.disconnect()
        await viewer2_socket.disconnect()

    async def test_duo_round_second_drawer_can_end_stroke_and_clear_canvas(self):
        self._seed_active_round_state(duo=True)
        stroke_data = {"type": "drawing.stroke", "payload": {"seed": "line"}}
        room_redis.append_canvas_stroke(
            self.fake_redis,
            self.room.join_code,
            json.dumps(stroke_data).encode(),
        )
        second_drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.second_drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )

        await _connect_and_assert_active_handshake(
            self,
            second_drawer_socket,
            self.room.join_code,
            replayed_drawing=[stroke_data],
        )
        await _connect_and_assert_active_handshake(
            self,
            viewer_socket,
            self.room.join_code,
            replayed_drawing=[stroke_data],
        )

        await second_drawer_socket.send_json_to({
            "type": "drawing.end_stroke",
            "payload": {},
        })
        end_stroke = await _receive_until_type(viewer_socket, "drawing.end_stroke")
        self.assertEqual(end_stroke["payload"], {})
        self.assertEqual(
            json.loads(
                room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code)[-1].decode()
            ),
            {"type": "drawing.end_stroke", "payload": {}},
        )

        await second_drawer_socket.send_json_to({"type": "drawing.clear"})
        clear_event = await _receive_until_type(viewer_socket, "drawing.clear")
        self.assertEqual(clear_event["payload"], {})
        self.assertEqual(
            room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code),
            [],
        )

        await second_drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_duo_round_disconnected_drawer_socket_cannot_draw(self):
        self._seed_active_round_state(duo=True)
        second_drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.second_drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )

        await _connect_and_assert_active_handshake(
            self,
            second_drawer_socket,
            self.room.join_code,
        )
        await _connect_and_assert_active_handshake(self, viewer_socket, self.room.join_code)

        room_redis.clear_session_presence(
            self.fake_redis,
            self.room.join_code,
            self.second_drawer_session_key,
        )
        await database_sync_to_async(
            Player.objects.filter(pk=self.second_drawer_player.id).update
        )(connection_status=Player.ConnectionStatus.DISCONNECTED)

        await second_drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": {"should": "not-broadcast"},
        })

        self.assertTrue(await viewer_socket.receive_nothing())
        self.assertEqual(
            room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code),
            [],
        )

        await second_drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_duo_round_stale_same_session_second_drawer_socket_cannot_draw(self):
        self._seed_active_round_state(duo=True)
        stale_drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.second_drawer_session_key),
        )
        active_drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.second_drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )

        await _connect_and_assert_active_handshake(
            self,
            stale_drawer_socket,
            self.room.join_code,
        )
        connection_key = room_redis._presence_connections_key(
            self.room.join_code,
            self.second_drawer_session_key,
        )
        stale_connection_ids = self.fake_redis.smembers(connection_key)
        self.assertEqual(len(stale_connection_ids), 1)
        stale_connection_id = next(iter(stale_connection_ids))
        if isinstance(stale_connection_id, bytes):
            stale_connection_id = stale_connection_id.decode()

        await _connect_and_assert_active_handshake(
            self,
            active_drawer_socket,
            self.room.join_code,
        )
        await _connect_and_assert_active_handshake(self, viewer_socket, self.room.join_code)

        self.assertTrue(
            room_redis.is_present(
                self.fake_redis,
                self.room.join_code,
                self.second_drawer_session_key,
            )
        )
        self.assertEqual(self.fake_redis.scard(connection_key), 2)
        room_redis.remove_presence(
            self.fake_redis,
            self.room.join_code,
            self.second_drawer_session_key,
            connection_id=stale_connection_id,
        )
        self.assertTrue(
            room_redis.is_present(
                self.fake_redis,
                self.room.join_code,
                self.second_drawer_session_key,
            )
        )
        self.assertFalse(
            room_redis.is_connection_present(
                self.fake_redis,
                self.room.join_code,
                self.second_drawer_session_key,
                stale_connection_id,
            )
        )
        self.assertEqual(self.fake_redis.scard(connection_key), 1)

        await _drain_output_queue_safe(stale_drawer_socket)
        await _drain_output_queue_safe(active_drawer_socket)
        await _drain_output_queue_safe(viewer_socket)

        await stale_drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": {"should": "not-broadcast"},
        })

        self.assertTrue(await active_drawer_socket.receive_nothing())
        self.assertTrue(await viewer_socket.receive_nothing())
        self.assertEqual(
            room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code),
            [],
        )

        await stale_drawer_socket.disconnect()
        await active_drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_drawing_clear_deletes_redis_snapshot(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        await _connect_and_assert_active_handshake(
            self,
            drawer_socket,
            self.room.join_code,
        )

        # Pre-seed a snapshot in Redis (list of JSON strings)
        stroke_data = {"type": "drawing.stroke", "payload": {"data": "seeded"}}
        room_redis.append_canvas_stroke(self.fake_redis, self.room.join_code, json.dumps(stroke_data).encode())

        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        await _connect_and_assert_active_handshake(
            self,
            viewer_socket,
            self.room.join_code,
            replayed_drawing=[stroke_data],
        )

        # Drawer clears the canvas
        await drawer_socket.send_json_to({"type": "drawing.clear"})
        
        # Viewer should receive the clear event broadcast after handshake sync.
        response = await _receive_until_type(viewer_socket, "drawing.clear")

        # Snapshot should be gone from Redis
        self.assertEqual(len(room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code)), 0)

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_drawing_not_allowed_in_lobby(self):
        # Move room back to lobby
        from games import redis as game_redis
        game_redis.clear_turn_state(self.fake_redis, self.room.join_code)
        
        from channels.db import database_sync_to_async
        self.room.status = Room.Status.LOBBY
        await database_sync_to_async(self.room.save)()

        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=False)
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=False)

        # "Drawer" tries to draw in lobby
        await drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": {"wont": "work"}
        })

        # Viewer should receive nothing.
        self.assertTrue(await viewer_socket.receive_nothing())

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()


@override_settings(
    SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True,
    CHANNEL_LAYERS={
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    },
)
class SnapshotSyncTests(TransactionTestCase):
    """Tests for canvas snapshot recovery on connection.

    InMemoryChannelLayer override matches DrawingEventTests / the other
    consumer test classes; see that class for rationale.
    """

    def setUp(self):
        from games import runtime as game_runtime

        game_runtime.reset_runtime_state_for_tests()
        room_consumers.reset_redis_client()

        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis
        game_runtime._redis_client = self.fake_redis

        self._orig_game_services_redis = game_services._get_redis_client
        game_services._get_redis_client = lambda: self.fake_redis

        # Set up a word pack for the room
        self.word_pack = WordPack.objects.create(name="Snapshot Pack")
        test_word = Word.objects.create(text="rocket")
        WordPackEntry.objects.create(word_pack=self.word_pack, word=test_word)

        self.room = Room.objects.create(
            name="Snapshot Room",
            join_code="SNAP1234",
            status=Room.Status.IN_PROGRESS,
            word_pack=self.word_pack,
        )
        
        # Create participants using async_to_sync
        self.drawer_session_key = async_to_sync(_create_room_member)(self.room.id, "Drawer")
        self.drawer_player = Player.objects.get(room=self.room, session_key=self.drawer_session_key)
        
        self.viewer_session_key = async_to_sync(_create_room_member)(self.room.id, "Viewer")
        self.viewer_player = Player.objects.get(room=self.room, session_key=self.viewer_session_key)
        
        self.game = Game.objects.create(room=self.room, status=GameStatus.IN_PROGRESS)
        self.game_word = GameWord.objects.create(game=self.game, text="testword")
        self.round = Round.objects.create(
            game=self.game,
            drawer_participant=self.drawer_player,
            drawer_nickname=self.drawer_player.display_name,
            selected_game_word=self.game_word,
            sequence_number=1,
        )

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
        room_consumers.reset_redis_client()
        game_services._get_redis_client = self._orig_game_services_redis
        super().tearDown()

    async def test_client_receives_snapshot_on_connect(self):
        self._seed_active_round_state()
        # Pre-seed a snapshot in Redis (list of JSON strings)
        snapshot_payload = {"type": "drawing.stroke", "payload": {"data": "test"}}
        room_redis.append_canvas_stroke(
            self.fake_redis, 
            self.room.join_code, 
            json.dumps(snapshot_payload).encode()
        )

        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        
        await _connect_and_assert_active_handshake(
            self,
            communicator,
            self.room.join_code,
            replayed_drawing=[snapshot_payload],
        )

        await communicator.disconnect()

    async def test_snapshot_accumulation(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(_TEST_APP, _ws_url(self.room.join_code), headers=_session_headers(self.drawer_session_key))
        sync_viewer = WebsocketCommunicator(_TEST_APP, _ws_url(self.room.join_code), headers=_session_headers(self.viewer_session_key))
        await _connect_and_assert_active_handshake(
            self,
            drawer_socket,
            self.room.join_code,
        )
        await _connect_and_assert_active_handshake(
            self,
            sync_viewer,
            self.room.join_code,
        )
        strokes = [{"type": "drawing.stroke", "payload": {"id": 1}}, {"type": "drawing.stroke", "payload": {"id": 2}}, {"type": "drawing.end_stroke", "payload": {}}]
        for s in strokes:
            await drawer_socket.send_json_to(s)
            await _receive_until_type(sync_viewer, s["type"])
        new_viewer_key = await _create_room_member(self.room.id, "Late Bob")
        viewer_socket = WebsocketCommunicator(_TEST_APP, _ws_url(self.room.join_code), headers=_session_headers(new_viewer_key))
        await _connect_and_assert_active_handshake(
            self,
            viewer_socket,
            self.room.join_code,
            replayed_drawing=strokes,
        )
        await drawer_socket.disconnect()
        await viewer_socket.disconnect()
        await sync_viewer.disconnect()

    async def test_snapshot_isolation_between_rounds(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(_TEST_APP, _ws_url(self.room.join_code), headers=_session_headers(self.drawer_session_key))
        await _connect_and_assert_active_handshake(
            self,
            drawer_socket,
            self.room.join_code,
        )
        sync_viewer = WebsocketCommunicator(_TEST_APP, _ws_url(self.room.join_code), headers=_session_headers(self.viewer_session_key))
        await _connect_and_assert_active_handshake(
            self,
            sync_viewer,
            self.room.join_code,
        )
        await drawer_socket.send_json_to({"type": "drawing.stroke", "payload": {"test": 1}})
        await _receive_until_type(sync_viewer, "drawing.stroke")
        self.assertEqual(len(room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code)), 1)
        # Completing a round progresses the game to the next round, so the test
        # must provide one unused snapshot word for the transition to stay valid.
        await database_sync_to_async(GameWord.objects.create)(game=self.game, text="planet")
        await database_sync_to_async(game_services.complete_round_due_to_timer)(self.round.id)
        self.assertEqual(
            room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code),
            [],
        )
        new_viewer_key = await _create_room_member(self.room.id, "Intermission Bob")
        v_socket = WebsocketCommunicator(_TEST_APP, _ws_url(self.room.join_code), headers=_session_headers(new_viewer_key))
        await _connect_and_assert_active_handshake(
            self,
            v_socket,
            self.room.join_code,
        )
        await drawer_socket.disconnect()
        await sync_viewer.disconnect()
        await v_socket.disconnect()

    async def test_no_snapshot_sent_if_redis_empty(self):
        # Redis is empty
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        
        await _connect_and_drain_initial_sync(communicator, self.room.join_code, expects_game_active=False)

        # Should receive nothing after the initial handshake
        self.assertTrue(await communicator.receive_nothing())

        await communicator.disconnect()
