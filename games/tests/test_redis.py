"""
Unit tests for games.redis

These tests use fakeredis so they run fully in-process without a real Redis
server. Each test method gets a fresh FakeRedis instance so tests are
completely isolated from each other.
"""

import fakeredis
from django.test import SimpleTestCase

from games import redis as game_redis

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_client() -> fakeredis.FakeRedis:
    """Return a fresh, isolated FakeRedis client."""
    return fakeredis.FakeRedis()

JOIN_CODE = "TESTGAME"
OTHER_CODE = "OTHERROOM"
PLAYER_A = 101
PLAYER_B = 102
PLAYER_C = 103

# ---------------------------------------------------------------------------
# Key builder tests
# ---------------------------------------------------------------------------

class GameRedisKeyTests(SimpleTestCase):
    def test_drawer_pool_key_format(self):
        self.assertEqual(game_redis._drawer_pool_key("MYROOM1"), "room:MYROOM1:game:drawer_pool")

    def test_turn_state_key_format(self):
        self.assertEqual(game_redis._turn_state_key("MYROOM1"), "room:MYROOM1:round:turn_state")

    def test_guess_state_key_format(self):
        self.assertEqual(game_redis._guess_state_key("MYROOM1", 5), "room:MYROOM1:round:5:guess_state")

    def test_round_payload_key_format(self):
        self.assertEqual(game_redis._round_payload_key("MYROOM1", "drawer"), "room:MYROOM1:round:payload:drawer")

    def test_deadline_key_format(self):
        self.assertEqual(game_redis._deadline_key("MYROOM1", "cleanup"), "room:MYROOM1:deadline:cleanup")

# ---------------------------------------------------------------------------
# Drawer Pool tests
# ---------------------------------------------------------------------------

class DrawerPoolTests(SimpleTestCase):
    def test_set_drawer_pool_adds_players_and_isolates(self):
        client = make_client()
        game_redis.set_drawer_pool(client, JOIN_CODE, [PLAYER_A, PLAYER_B])
        
        pool = game_redis.get_drawer_pool(client, JOIN_CODE)
        self.assertEqual(pool, {PLAYER_A, PLAYER_B})
        
        # Verify isolation
        other_pool = game_redis.get_drawer_pool(client, OTHER_CODE)
        self.assertEqual(other_pool, set())
        
        ttl = client.ttl(game_redis._drawer_pool_key(JOIN_CODE))
        self.assertAlmostEqual(ttl, game_redis.ROOM_RUNTIME_TTL, delta=5)

    def test_set_drawer_pool_overwrites_existing(self):
        client = make_client()
        game_redis.set_drawer_pool(client, JOIN_CODE, [PLAYER_A, PLAYER_B])
        game_redis.set_drawer_pool(client, JOIN_CODE, [PLAYER_C])
        pool = game_redis.get_drawer_pool(client, JOIN_CODE)
        self.assertEqual(pool, {PLAYER_C})

    def test_set_drawer_pool_with_empty_list_clears_it(self):
        client = make_client()
        game_redis.set_drawer_pool(client, JOIN_CODE, [PLAYER_A, PLAYER_B])
        game_redis.set_drawer_pool(client, JOIN_CODE, [])
        pool = game_redis.get_drawer_pool(client, JOIN_CODE)
        self.assertEqual(pool, set())

    def test_remove_from_drawer_pool(self):
        client = make_client()
        game_redis.set_drawer_pool(client, JOIN_CODE, [PLAYER_A, PLAYER_B])
        game_redis.remove_from_drawer_pool(client, JOIN_CODE, PLAYER_A)
        pool = game_redis.get_drawer_pool(client, JOIN_CODE)
        self.assertEqual(pool, {PLAYER_B})

    def test_remove_nonexistent_player_is_safe(self):
        client = make_client()
        game_redis.set_drawer_pool(client, JOIN_CODE, [PLAYER_A])
        game_redis.remove_from_drawer_pool(client, JOIN_CODE, PLAYER_B)
        pool = game_redis.get_drawer_pool(client, JOIN_CODE)
        self.assertEqual(pool, {PLAYER_A})

    def test_clear_drawer_pool(self):
        client = make_client()
        game_redis.set_drawer_pool(client, JOIN_CODE, [PLAYER_A])
        game_redis.set_drawer_pool(client, OTHER_CODE, [PLAYER_B])
        game_redis.clear_drawer_pool(client, JOIN_CODE)
        
        self.assertEqual(game_redis.get_drawer_pool(client, JOIN_CODE), set())
        # Make sure OTHER_CODE was untouched
        self.assertEqual(game_redis.get_drawer_pool(client, OTHER_CODE), {PLAYER_B})


# ---------------------------------------------------------------------------
# Turn State tests
# ---------------------------------------------------------------------------

class TurnStateTests(SimpleTestCase):
    def test_set_turn_state_stores_hash_and_isolates(self):
        client = make_client()
        state = {"status": "drawing", "round_id": 10}
        game_redis.set_turn_state(client, JOIN_CODE, state)
        
        result = game_redis.get_turn_state(client, JOIN_CODE)
        self.assertEqual(result, {"status": "drawing", "round_id": "10"})
        
        # Verify isolation
        self.assertEqual(game_redis.get_turn_state(client, OTHER_CODE), {})
        
        ttl = client.ttl(game_redis._turn_state_key(JOIN_CODE))
        self.assertAlmostEqual(ttl, game_redis.ROOM_RUNTIME_TTL, delta=5)

    def test_set_turn_state_overwrites(self):
        client = make_client()
        game_redis.set_turn_state(client, JOIN_CODE, {"status": "drawing"})
        game_redis.set_turn_state(client, JOIN_CODE, {"status": "intermission"})
        
        result = game_redis.get_turn_state(client, JOIN_CODE)
        self.assertEqual(result, {"status": "intermission"})

    def test_set_turn_state_empty_dict_clears(self):
        client = make_client()
        game_redis.set_turn_state(client, JOIN_CODE, {"status": "drawing"})
        game_redis.set_turn_state(client, JOIN_CODE, {})
        
        result = game_redis.get_turn_state(client, JOIN_CODE)
        self.assertEqual(result, {})

    def test_clear_turn_state(self):
        client = make_client()
        game_redis.set_turn_state(client, JOIN_CODE, {"status": "drawing"})
        game_redis.set_turn_state(client, OTHER_CODE, {"status": "drawing"})
        
        game_redis.clear_turn_state(client, JOIN_CODE)
        
        self.assertEqual(game_redis.get_turn_state(client, JOIN_CODE), {})
        self.assertEqual(game_redis.get_turn_state(client, OTHER_CODE), {"status": "drawing"})


# ---------------------------------------------------------------------------
# Guess State tests
# ---------------------------------------------------------------------------

class GuessStateTests(SimpleTestCase):
    def test_set_guess_state_stores_for_player_and_isolates(self):
        client = make_client()
        game_redis.set_guess_state(client, JOIN_CODE, 1, PLAYER_A, "correct")
        
        result = game_redis.get_guess_state(client, JOIN_CODE, 1, PLAYER_A)
        self.assertEqual(result, "correct")
        
        # Verify inter-room isolation
        self.assertIsNone(game_redis.get_guess_state(client, OTHER_CODE, 1, PLAYER_A))
        # Verify inter-round isolation within SAME room
        self.assertIsNone(game_redis.get_guess_state(client, JOIN_CODE, 2, PLAYER_A))
        
        ttl = client.ttl(game_redis._guess_state_key(JOIN_CODE, 1))
        self.assertAlmostEqual(ttl, game_redis.ROOM_RUNTIME_TTL, delta=5)

    def test_get_guess_state_absent(self):
        client = make_client()
        result = game_redis.get_guess_state(client, JOIN_CODE, 1, PLAYER_A)
        self.assertIsNone(result)

    def test_get_all_guess_states(self):
        client = make_client()
        game_redis.set_guess_state(client, JOIN_CODE, 1, PLAYER_A, "correct")
        game_redis.set_guess_state(client, JOIN_CODE, 1, PLAYER_B, "near_match")
        
        result = game_redis.get_all_guess_states(client, JOIN_CODE, 1)
        self.assertEqual(result, {PLAYER_A: "correct", PLAYER_B: "near_match"})

    def test_clear_guess_state(self):
        client = make_client()
        game_redis.set_guess_state(client, JOIN_CODE, 1, PLAYER_A, "correct")
        game_redis.set_guess_state(client, OTHER_CODE, 1, PLAYER_B, "near_match")
        game_redis.set_guess_state(client, JOIN_CODE, 2, PLAYER_C, "correct")
        
        game_redis.clear_guess_state(client, JOIN_CODE, 1)
        
        self.assertEqual(game_redis.get_all_guess_states(client, JOIN_CODE, 1), {})
        self.assertEqual(game_redis.get_all_guess_states(client, OTHER_CODE, 1), {PLAYER_B: "near_match"})
        self.assertEqual(game_redis.get_all_guess_states(client, JOIN_CODE, 2), {PLAYER_C: "correct"})


# ---------------------------------------------------------------------------
# Round Payload tests
# ---------------------------------------------------------------------------

class RoundPayloadTests(SimpleTestCase):
    def test_set_round_payloads_stores_json_and_isolates(self):
        client = make_client()
        drawer_payload = {"word": "apple"}
        guesser_payload = {"word": "_____"}
        
        game_redis.set_round_payloads(client, JOIN_CODE, drawer_payload, guesser_payload)
        
        d_res = game_redis.get_round_payload(client, JOIN_CODE, "drawer")
        g_res = game_redis.get_round_payload(client, JOIN_CODE, "guesser")
        
        self.assertEqual(d_res, drawer_payload)
        self.assertEqual(g_res, guesser_payload)
        
        # Room Isolation
        self.assertIsNone(game_redis.get_round_payload(client, OTHER_CODE, "drawer"))
        
        ttl = client.ttl(game_redis._round_payload_key(JOIN_CODE, "drawer"))
        self.assertAlmostEqual(ttl, game_redis.ROOM_RUNTIME_TTL, delta=5)

    def test_clear_round_payloads(self):
        client = make_client()
        game_redis.set_round_payloads(client, JOIN_CODE, {"w": "1"}, {"w": "2"})
        game_redis.set_round_payloads(client, OTHER_CODE, {"w": "X"}, {"w": "Y"})
        
        game_redis.clear_round_payloads(client, JOIN_CODE)
        
        self.assertIsNone(game_redis.get_round_payload(client, JOIN_CODE, "drawer"))
        self.assertIsNone(game_redis.get_round_payload(client, JOIN_CODE, "guesser"))
        
        # Verify isolation
        self.assertEqual(game_redis.get_round_payload(client, OTHER_CODE, "drawer"), {"w": "X"})


# ---------------------------------------------------------------------------
# Deadline tests
# ---------------------------------------------------------------------------

class DeadlineTests(SimpleTestCase):
    def test_set_deadline_stores_string_and_isolates(self):
        client = make_client()
        ts1 = "2026-04-10T00:00:00Z"
        ts2 = "2026-04-10T01:00:00Z"
        game_redis.set_deadline(client, JOIN_CODE, "cleanup", ts1)
        game_redis.set_deadline(client, JOIN_CODE, "turn_end", ts2)
        
        self.assertEqual(game_redis.get_deadline(client, JOIN_CODE, "cleanup"), ts1)
        self.assertEqual(game_redis.get_deadline(client, JOIN_CODE, "turn_end"), ts2)
        
        # Test isolation
        self.assertIsNone(game_redis.get_deadline(client, OTHER_CODE, "cleanup"))
        
        ttl = client.ttl(game_redis._deadline_key(JOIN_CODE, "cleanup"))
        self.assertAlmostEqual(ttl, game_redis.ROOM_RUNTIME_TTL, delta=5)

    def test_clear_deadline(self):
        client = make_client()
        game_redis.set_deadline(client, JOIN_CODE, "cleanup", "TIME")
        game_redis.set_deadline(client, OTHER_CODE, "cleanup", "OTHERTIME")
        
        game_redis.clear_deadline(client, JOIN_CODE, "cleanup")
        
        self.assertIsNone(game_redis.get_deadline(client, JOIN_CODE, "cleanup"))
        self.assertEqual(game_redis.get_deadline(client, OTHER_CODE, "cleanup"), "OTHERTIME")
