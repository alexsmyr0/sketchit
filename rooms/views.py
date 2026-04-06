import json
import random
import string

from django import forms
from django.db import transaction
from django.db.utils import IntegrityError
from django.http import HttpResponseNotAllowed, JsonResponse

from rooms.models import Player, Room


class CreateRoomForm(forms.Form):
    name = forms.CharField(max_length=255)
    visibility = forms.ChoiceField(choices=Room.Visibility.choices)
    display_name = forms.CharField(max_length=24)


class JoinRoomForm(forms.Form):
    display_name = forms.CharField(max_length=24)


# Parse the raw request body once and turn JSON format problems into API errors.
def _parse_json_payload(request):
    if request.content_type != "application/json":
        return None, JsonResponse(
            {"errors": {"body": ["Expected application/json request body."]}},
            status=400,
        )

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return None, JsonResponse(
            {"errors": {"body": ["Request body must be valid JSON."]}},
            status=400,
        )

    if not isinstance(payload, dict):
        return None, JsonResponse(
            {"errors": {"body": ["Request body must be a JSON object."]}},
            status=400,
        )

    return payload, None

# Create short room codes for URL-based room access.
def generate_join_code(length=8):
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choices(alphabet, k=length))

# Retry room creation if a generated join code collides with an existing room.
def _create_room_with_unique_join_code(*, name, visibility, max_attempts=10):
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

# Validate the request, create the room and host player, then return the room location.
def create_room(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    payload, error_response = _parse_json_payload(request)
    if error_response is not None:
        return error_response

    form = CreateRoomForm(payload)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)

    if request.session.session_key is None:
        request.session.save()

    session_key = request.session.session_key
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
        player = Player.objects.create(
            room=room,
            session_key=session_key,
            display_name=cleaned_data["display_name"],
            session_expires_at=request.session.get_expiry_date(),
        )
        room.host = player
        room.save()

    return JsonResponse(
        {
            "join_code": room.join_code,
            "room_url": f"/rooms/{room.join_code}/",
        },
        status=201,
    )
