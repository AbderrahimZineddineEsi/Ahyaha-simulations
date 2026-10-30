"""
Socket.IO for the app and the web pages. A JWT is needed to connect (`auth: {token}`), and the rooms follow the role:
`user:{id}` for everyone, `dispatchers` for dispatchers and admins, `hospital:{id}` for a hospital's staff, and
`incident:{id}` for a dispatcher who asks to watch one. Events: notification, incident.updated, ambulance.moved,
hospital.incoming.
"""
import logging

import socketio

from backend import auth, db
from backend.models import Notification, User

log = logging.getLogger("solvex")
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*", logger=False, engineio_logger=False)
tap = None  # a list in tests: (room, event, data) for everything sent


def user_room(uid):
    return f"user:{uid}"


async def emit(room, event, data):
    if tap is not None:
        tap.append((room, event, data))
    await sio.emit(event, data, room=room)


async def emit_user(uid, event, data):
    await emit(user_room(uid), event, data)


async def emit_dispatchers(event, data):
    await emit("dispatchers", event, data)


async def emit_incident(incident_id, event, data):
    await emit(f"incident:{incident_id}", event, data)


async def emit_hospital(hospital_id, event, data):
    await emit(f"hospital:{hospital_id}", event, data)


def is_online(uid):
    """Is a client listening for this user right now?"""
    try:
        return any(True for _ in sio.manager.get_participants("/", user_room(uid)))
    except KeyError:
        return False


@sio.event
async def connect(sid, environ, auth_data):
    token = (auth_data or {}).get("token") if isinstance(auth_data, dict) else None
    claims = auth.read_token(token) if token else None
    if claims is None:
        raise socketio.exceptions.ConnectionRefusedError("unauthorized")
    async with db.session() as s:
        user = await s.get(User, claims[0])
    if user is None:
        raise socketio.exceptions.ConnectionRefusedError("unauthorized")
    await sio.save_session(sid, {"id": user.id, "role": user.role, "hospital_id": user.hospital_id})
    await sio.enter_room(sid, user_room(user.id))
    if user.role in ("dispatcher", "admin"):
        await sio.enter_room(sid, "dispatchers")
    if user.role == "hospital" and user.hospital_id:
        await sio.enter_room(sid, f"hospital:{user.hospital_id}")


@sio.on("incident.watch")
async def watch(sid, data):
    session = await sio.get_session(sid)
    if session["role"] not in ("dispatcher", "admin"):
        return {"ok": False, "error": "forbidden"}
    await sio.enter_room(sid, f"incident:{int(data['id'])}")
    return {"ok": True}


@sio.on("notification.ack")
async def ack(sid, data):
    from backend.notifications import service
    session = await sio.get_session(sid)
    async with db.session_scope() as s:
        note = await s.get(Notification, int(data["id"]))
        if note is None or note.user_id != session["id"]:
            return {"ok": False, "error": "not_found"}
        service.mark_acked(note)
    return {"ok": True}
