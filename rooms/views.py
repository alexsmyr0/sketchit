import json
import random
import string

from django import forms
from django.db import transaction
from django.db.utils import IntegrityError
from django.http import HttpResponseNotAllowed, JsonResponse

from rooms.models import Player, Room


class CreateRoomForm(forms.Form):
    # This form validates the JSON fields required to create a brand-new room.
    name = forms.CharField(max_length=255)
    visibility = forms.ChoiceField(choices=Room.Visibility.choices)
    display_name = forms.CharField(max_length=24)


class JoinRoomForm(forms.Form):
    # Joining only needs the guest's display name.
    display_name = forms.CharField(max_length=24)


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
    # A guest session can only belong to one room at a time.
    if Player.objects.filter(session_key=session_key).exists():
        return JsonResponse(
            {"detail": "This guest session is already assigned to a room."},
            status=409,
        )

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

    try:
        # Generated join codes are uppercase, so we normalize user input before
        # querying to allow lowercase requests like "abc12345".
        room = Room.objects.get(join_code=join_code.upper())
    except Room.DoesNotExist:
        return JsonResponse({"detail": "Room not found."}, status=404)

    # If the session already has a Player, we either reuse it for the same room
    # or reject the request if it belongs to a different room.
    player = Player.objects.filter(session_key=session_key).first()
    if player is not None:
        if player.room_id != room.id:
            return JsonResponse(
                {"detail": "This guest session is already assigned to a room."},
                status=409,
            )

        # Rejoining the same room should not create a duplicate participant or
        # change the original display name, but it should refresh the session
        # expiry we store on the player record.
        player.session_expires_at = request.session.get_expiry_date()
        player.save(update_fields=["session_expires_at", "updated_at"])
        return _build_room_response(room, status=200)

    # Capacity only matters for brand-new joins, not same-session rejoin reuse.
    if room.participants.count() >= room.max_players:
        return JsonResponse(
            {"detail": "This room is full."},
            status=409,
        )

    # No player exists for this session yet, so create a new participant row.
    Player.objects.create(
        room=room,
        session_key=session_key,
        display_name=form.cleaned_data["display_name"],
        session_expires_at=request.session.get_expiry_date(),
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
    if not room.participants.filter(session_key=session_key).exists():
        return JsonResponse(
            {"detail": "This guest session is not a participant in this room."},
            status=403,
        )

    return _build_room_lobby_state_response(room)
