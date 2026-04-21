import json
import random
import string

from django import forms
from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.db.utils import IntegrityError
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
import redis

from games.services import StartGameError, start_game_for_room
from rooms.models import Player, Room
from rooms.services import (
    delete_room_if_empty_grace_expired,
    get_empty_room_cleanup_deadline,
    purge_expired_participants_for_session,
    restore_room_from_empty_grace,
    schedule_host_changed_broadcast_after_commit,
    schedule_room_state_broadcast_after_commit,
)


_redis_client = None


class CreateRoomForm(forms.Form):
    # This form validates the JSON fields required to create a brand-new room.
    name = forms.CharField(max_length=255)
    visibility = forms.ChoiceField(choices=Room.Visibility.choices)
    display_name = forms.CharField(max_length=24)


class JoinRoomForm(forms.Form):
    # Joining only needs the guest's display name.
    display_name = forms.CharField(max_length=24)


class UpdateLobbySettingsForm(forms.Form):
    name = forms.CharField(max_length=255)
    visibility = forms.ChoiceField(choices=Room.Visibility.choices)


def _get_room_runtime_redis_client() -> redis.Redis:
    """Return a cached Redis client for room lifecycle runtime helpers.

    The join flow occasionally needs Redis only for empty-room grace cleanup.
    Caching keeps that path cheap without pushing Redis client construction
    into every request that touches an empty-grace room.
    """

    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.REDIS_URL)
    return _redis_client


def _parse_json_payload(request):
    # Both create_room and join_room expect JSON requests.
    if request.content_type != "application/json":
        return None, JsonResponse(
            {"errors": {"body": ["Expected application/json request body."]}},
            status=400,
        )

    try:
        # request.body is raw bytes; json.loads handles bytes or strings.
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return None, JsonResponse(
            {"errors": {"body": ["Request body must be valid JSON."]}},
            status=400,
        )

    # We only accept a top-level JSON object like {"display_name": "Alex"}.
    if not isinstance(payload, dict):
        return None, JsonResponse(
            {"errors": {"body": ["Request body must be a JSON object."]}},
            status=400,
        )

    return payload, None


def _get_or_create_session_key(request):
    # A guest may not have a session yet. Saving the session forces Django to
    # create one and assign a stable session_key we can use as guest identity.
    if request.session.session_key is None:
        request.session.save()

    return request.session.session_key


def _build_room_response(room, *, status):
    # Both create and join return the same small payload so clients can treat
    # the success responses consistently.
    return JsonResponse(
        {
            "join_code": room.join_code,
            "room_url": f"/rooms/{room.join_code}/",
        },
        status=status,
    )


def _build_room_assignment_conflict_response(room):
    """Return a recoverable conflict payload for an already-owned valid room.

    The create/join entry flow still treats this as a conflict because the
    guest cannot own two rooms at once. Including ``room_url`` lets the browser
    recover by navigating back into the authoritative existing room instead of
    leaving the guest stuck on the entry page.
    """

    return JsonResponse(
        {
            "detail": "This guest session is already assigned to a room.",
            "join_code": room.join_code,
            "room_url": f"/rooms/{room.join_code}/",
        },
        status=409,
    )


def _serialize_host(player):
    if player is None:
        return None

    return {
        "id": player.id,
        "display_name": player.display_name,
    }


def _serialize_participant(player):
    return {
        "id": player.id,
        "display_name": player.display_name,
        "connection_status": player.connection_status,
        "participation_status": player.participation_status,
    }


def _serialize_public_room(room):
    return {
        "name": room.name,
        "join_code": room.join_code,
        "visibility": room.visibility,
        "status": room.status,
        "participant_count": room.participant_count,
        "max_players": room.max_players,
        "host": _serialize_host(room.host),
        "room_url": f"/rooms/{room.join_code}/",
    }


def _build_room_lobby_state_response(room):
    participants = room.participants.order_by("created_at", "id")
    return JsonResponse(
        {
            "room": {
                "name": room.name,
                "join_code": room.join_code,
                "visibility": room.visibility,
                "status": room.status,
            },
            "host": _serialize_host(room.host),
            "participants": [_serialize_participant(player) for player in participants],
        },
        status=200,
    )


def _request_prefers_html(request):
    accept_header = request.headers.get("Accept", "")
    normalized_accept = accept_header.lower()
    return "text/html" in normalized_accept and "application/json" not in normalized_accept


def generate_join_code(length=8):
    # Join codes are short and URL-friendly rather than secret passwords.
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choices(alphabet, k=length))


def _create_room_with_unique_join_code(*, name, visibility, max_attempts=10):
    # Because join_code must be unique, we retry if random generation collides
    # with an existing row.
    for _ in range(max_attempts):
        join_code = generate_join_code()
        try:
            with transaction.atomic():
                return Room.objects.create(
                    name=name,
                    visibility=visibility,
                    join_code=join_code,
                    status=Room.Status.LOBBY,
                )
        except IntegrityError:
            if Room.objects.filter(join_code=join_code).exists():
                continue
            raise

    raise RuntimeError("Could not generate a unique join code.")


def create_room(request):
    # This endpoint only accepts POST because it creates server-side state.
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    payload, error_response = _parse_json_payload(request)
    if error_response is not None:
        return error_response

    form = CreateRoomForm(payload)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)

    session_key = _get_or_create_session_key(request)
    # Clean up stale ownership for this browser session before enforcing the
    # one-room-at-a-time rule. Persistent MySQL rows can survive app restarts
    # after the underlying Django session has already expired.
    purge_expired_participants_for_session(
        redis_client=_get_room_runtime_redis_client(),
        session_key=session_key,
    )
    existing_player = (
        Player.objects.select_related("room")
        .filter(session_key=session_key)
        .order_by("created_at", "id")
        .first()
    )
    if existing_player is not None:
        return _build_room_assignment_conflict_response(existing_player.room)

    cleaned_data = form.cleaned_data

    with transaction.atomic():
        room = _create_room_with_unique_join_code(
            name=cleaned_data["name"],
            visibility=cleaned_data["visibility"],
        )
        # The room creator immediately becomes the first participant.
        player = Player.objects.create(
            room=room,
            session_key=session_key,
            display_name=cleaned_data["display_name"],
            # Room membership starts disconnected until the browser opens the
            # room socket and the realtime layer confirms live presence.
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            session_expires_at=request.session.get_expiry_date(),
        )
        # The first participant is also the initial host.
        room.host = player
        room.save()

    return _build_room_response(room, status=201)


def join_room(request, join_code):
    # This endpoint changes room membership, so it is also POST-only.
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    payload, error_response = _parse_json_payload(request)
    if error_response is not None:
        return error_response

    form = JoinRoomForm(payload)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)

    # The Django session is our guest identity for the MVP.
    session_key = _get_or_create_session_key(request)
    room_runtime_redis_client = _get_room_runtime_redis_client()

    with transaction.atomic():
        # Entry flow cleanup is session-scoped on purpose: remove stale rows
        # that no longer belong to a live Django session before deciding
        # whether this request is a valid rejoin or a real cross-room conflict.
        purge_expired_participants_for_session(
            redis_client=room_runtime_redis_client,
            session_key=session_key,
        )

        try:
            # Generated join codes are uppercase, so we normalize user input
            # before querying to allow lowercase requests like "abc12345".
            room = (
                Room.objects.select_for_update()
                .select_related("host")
                .get(join_code=join_code.upper())
            )
        except Room.DoesNotExist:
            return JsonResponse({"detail": "Room not found."}, status=404)

        if room.status == Room.Status.EMPTY_GRACE:
            current_time = timezone.now()
            if (
                room.empty_since is not None
                and current_time >= get_empty_room_cleanup_deadline(
                    empty_since=room.empty_since,
                )
            ):
                # A join that arrives after the grace window should not revive a
                # zombie room just because the async cleanup path has not run yet.
                delete_room_if_empty_grace_expired(
                    redis_client=room_runtime_redis_client,
                    room_id=room.id,
                    now=current_time,
                )
                return JsonResponse({"detail": "Room not found."}, status=404)

            restore_room_from_empty_grace(
                redis_client=room_runtime_redis_client,
                room_id=room.id,
            )
            room.refresh_from_db()

        # If the session already has a Player, we either reuse it for the same room
        # or reject the request if it belongs to a different room.
        player = (
            Player.objects.select_related("room")
            .filter(session_key=session_key)
            .order_by("created_at", "id")
            .first()
        )
        if player is not None:
            if player.room_id != room.id:
                # Keep the conflict semantics, but include a recovery target so
                # the entry page can send the guest back to the room they still
                # validly own instead of leaving them stuck at a dead end.
                return _build_room_assignment_conflict_response(player.room)

            # Rejoining the same room should not create a duplicate participant or
            # change the original display name, but it should refresh the session
            # expiry we store on the player record.
            player.session_expires_at = request.session.get_expiry_date()
            player.save(update_fields=["session_expires_at", "updated_at"])
            return _build_room_response(room, status=200)

        # Capacity only matters for brand-new joins, not same-session rejoin reuse.
        #
        # Concurrency note: this count is safe against a double-join race even
        # though it reads the Player table (which is not directly locked). The
        # select_for_update() on the Room row above serializes concurrent
        # join_room requests targeting the same room — request B blocks on the
        # row lock until request A commits its Player INSERT. When B then runs
        # this count, it sees A's newly-inserted row and correctly rejects the
        # over-capacity join. Under MySQL InnoDB REPEATABLE READ the snapshot
        # for B's non-locking reads is established after the locking SELECT,
        # i.e. after A's commit is visible.
        current_participant_count = Player.objects.filter(room_id=room.id).count()
        if current_participant_count >= room.max_players:
            return JsonResponse(
                {"detail": "This room is full."},
                status=409,
            )

        # No player exists for this session yet, so create a new participant row.
        #
        # A-07: players who join while a game is already in progress are not
        # eligible to guess or draw for the current turn. Marking them as
        # SPECTATING prevents the guess pipeline and drawer-pool logic from
        # treating them as full participants until the next round transition
        # promotes them to PLAYING.
        joining_mid_game = room.status == Room.Status.IN_PROGRESS
        player = Player.objects.create(
            room=room,
            session_key=session_key,
            display_name=form.cleaned_data["display_name"],
            # Joining the room via HTTP alone should not count as live presence.
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            session_expires_at=request.session.get_expiry_date(),
            participation_status=(
                Player.ParticipationStatus.SPECTATING
                if joining_mid_game
                else Player.ParticipationStatus.PLAYING
            ),
        )
        # Empty-grace rooms have no active membership, so the first returning
        # participant must become the new host to make the lobby usable again.
        if room.host_id is None:
            room.host = player
            room.save(update_fields=["host", "updated_at"])
            schedule_host_changed_broadcast_after_commit(
                join_code=room.join_code,
                host=room.host,
            )
        # Only a brand-new participant creation should fan out an A-06 lobby
        # update. Same-session rejoin reuse returns early above and stays quiet.
        schedule_room_state_broadcast_after_commit(
            join_code=room.join_code,
            room_id=room.id,
        )

    return _build_room_response(room, status=201)


def room_lobby_state(request, join_code):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    try:
        room = Room.objects.select_related("host").get(join_code=join_code.upper())
    except Room.DoesNotExist:
        return JsonResponse({"detail": "Room not found."}, status=404)

    session_key = _get_or_create_session_key(request)
    participant = room.participants.filter(session_key=session_key).first()
    if participant is None:
        return JsonResponse(
            {"detail": "This guest session is not a participant in this room."},
            status=403,
        )

    if _request_prefers_html(request):
        participants = room.participants.order_by("created_at", "id")
        return render(
            request,
            "rooms/room_lobby.html",
            {
                "room": room,
                "host": room.host,
                "participant": participant,
                "participants": participants,
            },
        )

    return _build_room_lobby_state_response(room)


@transaction.atomic
def update_lobby_settings(request, join_code):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        room = (
            Room.objects.select_for_update()
            .select_related("host")
            .get(join_code=join_code.upper())
        )
    except Room.DoesNotExist:
        return JsonResponse({"detail": "Room not found."}, status=404)

    session_key = _get_or_create_session_key(request)
    requester = room.participants.filter(session_key=session_key).first()
    if requester is None:
        return JsonResponse(
            {"detail": "This guest session is not a participant in this room."},
            status=403,
        )

    if room.host_id != requester.id:
        return JsonResponse(
            {"detail": "Only the room host can update settings."},
            status=403,
        )

    if room.status != Room.Status.LOBBY:
        return JsonResponse(
            {"detail": "Room settings can only be updated while in the lobby."},
            status=409,
        )

    payload, error_response = _parse_json_payload(request)
    if error_response is not None:
        return error_response

    form = UpdateLobbySettingsForm(payload)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)

    room.name = form.cleaned_data["name"]
    room.visibility = form.cleaned_data["visibility"]
    room.save(update_fields=["name", "visibility", "updated_at"])

    return _build_room_lobby_state_response(room)


def public_room_directory(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    public_rooms = (
        Room.objects.select_related("host")
        .annotate(participant_count=Count("participants"))
        .filter(visibility=Room.Visibility.PUBLIC)
        .order_by("-created_at", "-id")
    )

    return JsonResponse(
        {
            "rooms": [_serialize_public_room(room) for room in public_rooms],
        },
        status=200,
    )


def start_game(request, join_code):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        room = Room.objects.select_related("host").get(join_code=join_code.upper())
    except Room.DoesNotExist:
        return JsonResponse({"detail": "Room not found."}, status=404)

    session_key = _get_or_create_session_key(request)
    requester = room.participants.filter(session_key=session_key).first()
    if requester is None:
        return JsonResponse(
            {"detail": "This guest session is not a participant in this room."},
            status=403,
        )

    if room.host_id != requester.id:
        return JsonResponse(
            {"detail": "Only the room host can start a game."},
            status=403,
        )

    try:
        started_game = start_game_for_room(room)
    except StartGameError as exc:
        return JsonResponse({"detail": str(exc)}, status=409)

    room.refresh_from_db(fields=["status"])
    game = started_game.game
    first_round = started_game.first_round

    return JsonResponse(
        {
            "game_id": game.id,
            "round_id": first_round.id,
            "room_status": room.status,
            "room": {
                "join_code": room.join_code,
                "status": room.status,
            },
            "game": {
                "id": game.id,
                "status": game.status,
                "word_count": game.snapshot_words.count(),
            },
            "first_round": {
                "id": first_round.id,
                "sequence_number": first_round.sequence_number,
                "status": first_round.status,
                "drawer_participant_id": first_round.drawer_participant_id,
                "drawer_nickname": first_round.drawer_nickname,
                "selected_game_word_id": first_round.selected_game_word_id,
            },
        },
        status=201,
    )


@ensure_csrf_cookie
def room_entry_page(request):
    public_rooms = (
        Room.objects.select_related("host")
        .annotate(participant_count=Count("participants"))
        .filter(visibility=Room.Visibility.PUBLIC)
        .order_by("-created_at", "-id")
    )

    return render(
        request,
        "rooms/room_entry.html",
        {
            "public_rooms": [_serialize_public_room(room) for room in public_rooms],
        },
    )
