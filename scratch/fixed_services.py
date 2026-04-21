@transaction.atomic
def connect_participant(
    redis_client,
    player: Player,
    connection_id: str,
) -> bool:
    """Mark one participant as connected for an active socket.

    Redis tracks the per-socket presence details. MySQL stores the durable
    aggregate view that the participant is currently connected.
    """

    _validate_room_presence_identity(
        player=player,
        join_code=player.room.join_code,
        session_key=player.session_key,
    )
    # Always use the canonical join code from the database row when touching
    # Redis so callers cannot accidentally split presence across differently
    # cased keys like "ABCD1234" and "abcd1234".
    room_join_code = player.room.join_code
    previous_connection_status = player.connection_status

    room_redis.add_presence(
        redis_client,
        room_join_code,
        player.session_key,
        connection_id=connection_id,
    )
    Player.objects.filter(pk=player.id).update(
        connection_status=Player.ConnectionStatus.CONNECTED,
        last_seen_at=timezone.now(),
    )
    status_changed = (previous_connection_status != Player.ConnectionStatus.CONNECTED)

    return status_changed


@transaction.atomic
def disconnect_participant(
    redis_client,
    player: Player,
    connection_id: str,
) -> None:
    """Mark one participant socket as disconnected.

    A participant only becomes fully disconnected after the final socket for
    that same session leaves the room. Temporary disconnects should not delete
    the participant row or trigger host reassignment; those behaviors belong to
    a permanent leave only.
    """

    _validate_room_presence_identity(
        player=player,
        join_code=player.room.join_code,
        session_key=player.session_key,
    )
    room_join_code = player.room.join_code
    session_key = player.session_key

    room_redis.remove_presence(
        redis_client,
        room_join_code,
        session_key,
        connection_id=connection_id,
    )
    
    # Redis knows about individual sockets, while MySQL stores the room-level
    # answer of whether this participant is still connected anywhere right now.
    connection_still_present = room_redis.is_present(
        redis_client,
        room_join_code,
        session_key,
    )
    
    Player.objects.filter(pk=player.id).update(
        connection_status=(
            Player.ConnectionStatus.CONNECTED
            if connection_still_present
            else Player.ConnectionStatus.DISCONNECTED
        ),
        last_seen_at=timezone.now(),
    )
