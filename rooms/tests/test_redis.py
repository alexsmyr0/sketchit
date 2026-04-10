"""
Unit tests for rooms.redis

These tests use fakeredis so they run fully in-process without a real Redis
server.  Each test method gets a fresh FakeRedis instance so tests are
completely isolated from each other.
"""

import fakeredis
from django.test import SimpleTestCase

from rooms import redis as room_redis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_client() -> fakeredis.FakeRedis:
    """Return a fresh, isolated FakeRedis client."""
    return fakeredis.FakeRedis()


JOIN_CODE = "ABC12345"
SESSION_A = "session-aaaa"
SESSION_B = "session-bbbb"
SESSION_C = "session-cccc"


# ---------------------------------------------------------------------------
# Key builder tests
# ---------------------------------------------------------------------------


class RoomRedisKeyTests(SimpleTestCase):
    """Verify the internal key naming convention."""

    def test_presence_key_format(self):
        self.assertEqual(room_redis._presence_key("MYROOM1"), "room:MYROOM1:presence")

    def test_canvas_key_format(self):
        self.assertEqual(room_redis._canvas_key("MYROOM1"), "room:MYROOM1:canvas")

    def test_presence_connections_key_format(self):
        self.assertEqual(
            room_redis._presence_connections_key("MYROOM1", SESSION_A),
            f"room:MYROOM1:presence:{SESSION_A}:connections",
        )


# ---------------------------------------------------------------------------
# Presence tests
# ---------------------------------------------------------------------------


class AddPresenceTests(SimpleTestCase):
    def test_add_presence_adds_session_to_set(self):
        client = make_client()
        room_redis.add_presence(client, JOIN_CODE, SESSION_A)
        members = client.smembers(room_redis._presence_key(JOIN_CODE))
        self.assertIn(SESSION_A.encode(), members)

    def test_add_presence_sets_ttl_on_key(self):
        client = make_client()
        room_redis.add_presence(client, JOIN_CODE, SESSION_A)
        ttl = client.ttl(room_redis._presence_key(JOIN_CODE))
        # TTL should be approximately ROOM_KEY_TTL; allow ±5 s for execution time.
        self.assertAlmostEqual(ttl, room_redis.ROOM_KEY_TTL, delta=5)

    def test_add_presence_allows_multiple_sessions(self):
        client = make_client()
        room_redis.add_presence(client, JOIN_CODE, SESSION_A)
        room_redis.add_presence(client, JOIN_CODE, SESSION_B)
        room_redis.add_presence(client, JOIN_CODE, SESSION_C)
        members = client.smembers(room_redis._presence_key(JOIN_CODE))
        self.assertEqual(len(members), 3)

    def test_add_presence_for_multiple_rooms_are_independent(self):
        client = make_client()
        room_redis.add_presence(client, "ROOM0001", SESSION_A)
        room_redis.add_presence(client, "ROOM0002", SESSION_B)
        self.assertEqual(client.scard(room_redis._presence_key("ROOM0001")), 1)
        self.assertEqual(client.scard(room_redis._presence_key("ROOM0002")), 1)

    def test_add_presence_tracks_multiple_connections_for_same_session(self):
        client = make_client()
        room_redis.add_presence(
            client,
            JOIN_CODE,
            SESSION_A,
            connection_id="chan-1",
        )
        room_redis.add_presence(
            client,
            JOIN_CODE,
            SESSION_A,
            connection_id="chan-2",
        )

        self.assertEqual(room_redis.get_presence(client, JOIN_CODE), {SESSION_A})
        self.assertEqual(
            client.scard(room_redis._presence_connections_key(JOIN_CODE, SESSION_A)),
            2,
        )


class RemovePresenceTests(SimpleTestCase):
    def test_remove_presence_removes_session_from_set(self):
        client = make_client()
        room_redis.add_presence(client, JOIN_CODE, SESSION_A)
        room_redis.add_presence(client, JOIN_CODE, SESSION_B)

        room_redis.remove_presence(client, JOIN_CODE, SESSION_A)

        members = room_redis.get_presence(client, JOIN_CODE)
        self.assertNotIn(SESSION_A, members)
        self.assertIn(SESSION_B, members)

    def test_remove_presence_is_safe_when_session_is_absent(self):
        client = make_client()
        # Should not raise even if the key or member doesn't exist.
        room_redis.remove_presence(client, JOIN_CODE, SESSION_A)

    def test_remove_presence_is_safe_when_key_does_not_exist(self):
        client = make_client()
        room_redis.remove_presence(client, "NOSUCHROOM", SESSION_A)

    def test_remove_presence_with_connection_id_keeps_session_until_last_socket_leaves(self):
        client = make_client()
        room_redis.add_presence(
            client,
            JOIN_CODE,
            SESSION_A,
            connection_id="chan-1",
        )
        room_redis.add_presence(
            client,
            JOIN_CODE,
            SESSION_A,
            connection_id="chan-2",
        )

        room_redis.remove_presence(
            client,
            JOIN_CODE,
            SESSION_A,
            connection_id="chan-1",
        )

        self.assertTrue(room_redis.is_present(client, JOIN_CODE, SESSION_A))
        self.assertEqual(
            client.scard(room_redis._presence_connections_key(JOIN_CODE, SESSION_A)),
            1,
        )

        room_redis.remove_presence(
            client,
            JOIN_CODE,
            SESSION_A,
            connection_id="chan-2",
        )

        self.assertFalse(room_redis.is_present(client, JOIN_CODE, SESSION_A))
        self.assertEqual(
            client.exists(room_redis._presence_connections_key(JOIN_CODE, SESSION_A)),
            0,
        )


class GetPresenceTests(SimpleTestCase):
    def test_get_presence_returns_connected_sessions(self):
        client = make_client()
        room_redis.add_presence(client, JOIN_CODE, SESSION_A)
        room_redis.add_presence(client, JOIN_CODE, SESSION_B)

        result = room_redis.get_presence(client, JOIN_CODE)

        self.assertEqual(result, {SESSION_A, SESSION_B})

    def test_get_presence_returns_empty_set_when_key_absent(self):
        client = make_client()
        result = room_redis.get_presence(client, JOIN_CODE)
        self.assertEqual(result, set())

    def test_get_presence_returns_str_values_not_bytes(self):
        client = make_client()
        room_redis.add_presence(client, JOIN_CODE, SESSION_A)

        result = room_redis.get_presence(client, JOIN_CODE)

        for value in result:
            self.assertIsInstance(value, str)


class IsPresentTests(SimpleTestCase):
    def test_is_present_returns_true_for_connected_session(self):
        client = make_client()
        room_redis.add_presence(client, JOIN_CODE, SESSION_A)
        self.assertTrue(room_redis.is_present(client, JOIN_CODE, SESSION_A))

    def test_is_present_returns_false_for_absent_session(self):
        client = make_client()
        room_redis.add_presence(client, JOIN_CODE, SESSION_A)
        self.assertFalse(room_redis.is_present(client, JOIN_CODE, SESSION_B))

    def test_is_present_returns_false_when_key_does_not_exist(self):
        client = make_client()
        self.assertFalse(room_redis.is_present(client, JOIN_CODE, SESSION_A))

    def test_is_present_returns_false_after_session_removed(self):
        client = make_client()
        room_redis.add_presence(client, JOIN_CODE, SESSION_A)
        room_redis.remove_presence(client, JOIN_CODE, SESSION_A)
        self.assertFalse(room_redis.is_present(client, JOIN_CODE, SESSION_A))


class ClearPresenceTests(SimpleTestCase):
    def test_clear_presence_removes_the_entire_set(self):
        client = make_client()
        room_redis.add_presence(client, JOIN_CODE, SESSION_A)
        room_redis.add_presence(client, JOIN_CODE, SESSION_B)

        room_redis.clear_presence(client, JOIN_CODE)

        self.assertEqual(room_redis.get_presence(client, JOIN_CODE), set())

    def test_clear_presence_is_safe_when_key_absent(self):
        client = make_client()
        room_redis.clear_presence(client, JOIN_CODE)  # must not raise

    def test_clear_presence_does_not_affect_other_rooms(self):
        client = make_client()
        room_redis.add_presence(client, "ROOM0001", SESSION_A)
        room_redis.add_presence(client, "ROOM0002", SESSION_B)

        room_redis.clear_presence(client, "ROOM0001")

        self.assertEqual(room_redis.get_presence(client, "ROOM0001"), set())
        self.assertIn(SESSION_B, room_redis.get_presence(client, "ROOM0002"))

    def test_clear_presence_removes_per_session_connection_keys(self):
        client = make_client()
        room_redis.add_presence(
            client,
            JOIN_CODE,
            SESSION_A,
            connection_id="chan-1",
        )

        room_redis.clear_presence(client, JOIN_CODE)

        self.assertEqual(
            client.exists(room_redis._presence_connections_key(JOIN_CODE, SESSION_A)),
            0,
        )


class ClearSessionPresenceTests(SimpleTestCase):
    def test_clear_session_presence_removes_one_session_and_its_connections(self):
        client = make_client()
        room_redis.add_presence(
            client,
            JOIN_CODE,
            SESSION_A,
            connection_id="chan-1",
        )
        room_redis.add_presence(
            client,
            JOIN_CODE,
            SESSION_B,
            connection_id="chan-2",
        )

        room_redis.clear_session_presence(client, JOIN_CODE, SESSION_A)

        self.assertFalse(room_redis.is_present(client, JOIN_CODE, SESSION_A))
        self.assertTrue(room_redis.is_present(client, JOIN_CODE, SESSION_B))
        self.assertEqual(
            client.exists(room_redis._presence_connections_key(JOIN_CODE, SESSION_A)),
            0,
        )

    def test_clear_session_presence_is_safe_when_session_is_absent(self):
        client = make_client()
        room_redis.clear_session_presence(client, JOIN_CODE, SESSION_A)


# ---------------------------------------------------------------------------
# Canvas snapshot tests
# ---------------------------------------------------------------------------


class SetCanvasSnapshotTests(SimpleTestCase):
    def test_set_canvas_snapshot_stores_bytes(self):
        client = make_client()
        data = b"\x89PNG\r\nfake-image-data"
        room_redis.set_canvas_snapshot(client, JOIN_CODE, data)

        stored = client.get(room_redis._canvas_key(JOIN_CODE))
        self.assertEqual(stored, data)

    def test_set_canvas_snapshot_sets_ttl(self):
        client = make_client()
        room_redis.set_canvas_snapshot(client, JOIN_CODE, b"")
        ttl = client.ttl(room_redis._canvas_key(JOIN_CODE))
        self.assertAlmostEqual(ttl, room_redis.ROOM_KEY_TTL, delta=5)

    def test_set_canvas_snapshot_overwrites_previous_value(self):
        client = make_client()
        room_redis.set_canvas_snapshot(client, JOIN_CODE, b"old-data")
        room_redis.set_canvas_snapshot(client, JOIN_CODE, b"new-data")

        result = room_redis.get_canvas_snapshot(client, JOIN_CODE)
        self.assertEqual(result, b"new-data")

    def test_set_canvas_snapshot_placeholder_stores_empty_bytes(self):
        client = make_client()
        room_redis.set_canvas_snapshot(client, JOIN_CODE, b"")

        result = room_redis.get_canvas_snapshot(client, JOIN_CODE)
        self.assertEqual(result, b"")


class GetCanvasSnapshotTests(SimpleTestCase):
    def test_get_canvas_snapshot_returns_stored_bytes(self):
        client = make_client()
        data = b"some-canvas-payload"
        room_redis.set_canvas_snapshot(client, JOIN_CODE, data)

        result = room_redis.get_canvas_snapshot(client, JOIN_CODE)

        self.assertEqual(result, data)

    def test_get_canvas_snapshot_returns_none_when_key_absent(self):
        client = make_client()
        result = room_redis.get_canvas_snapshot(client, JOIN_CODE)
        self.assertIsNone(result)

    def test_get_canvas_snapshot_returns_bytes_type(self):
        client = make_client()
        room_redis.set_canvas_snapshot(client, JOIN_CODE, b"data")
        result = room_redis.get_canvas_snapshot(client, JOIN_CODE)
        self.assertIsInstance(result, bytes)


class ClearCanvasSnapshotTests(SimpleTestCase):
    def test_clear_canvas_snapshot_deletes_the_key(self):
        client = make_client()
        room_redis.set_canvas_snapshot(client, JOIN_CODE, b"data")

        room_redis.clear_canvas_snapshot(client, JOIN_CODE)

        self.assertIsNone(room_redis.get_canvas_snapshot(client, JOIN_CODE))

    def test_clear_canvas_snapshot_is_safe_when_key_absent(self):
        client = make_client()
        room_redis.clear_canvas_snapshot(client, JOIN_CODE)  # must not raise

    def test_clear_canvas_snapshot_does_not_affect_other_rooms(self):
        client = make_client()
        room_redis.set_canvas_snapshot(client, "ROOM0001", b"data-1")
        room_redis.set_canvas_snapshot(client, "ROOM0002", b"data-2")

        room_redis.clear_canvas_snapshot(client, "ROOM0001")

        self.assertIsNone(room_redis.get_canvas_snapshot(client, "ROOM0001"))
        self.assertEqual(room_redis.get_canvas_snapshot(client, "ROOM0002"), b"data-2")
