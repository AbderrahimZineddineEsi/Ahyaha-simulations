"""Accounts, profiles, locations, and the privacy rule: a medical profile is shown only for a reason (PROJECT.md S13)."""
from datetime import timedelta

from sqlalchemy import select

from backend import auth, bus, clock
from backend.config import settings
from backend.geo.distance import haversine_m
from backend.models import Device, User

PROFILE_FIELDS = ("age", "sex", "blood_type", "conditions", "emergency_contact_name", "emergency_contact_phone")
EDITABLE = ("name", *PROFILE_FIELDS, "skills", "is_helper")


def own_view(u: User):
    """Everything about the user, for the user."""
    return {"id": u.id, "role": u.role, "phone": u.phone, "name": u.name, "skills": u.skills, "is_helper": u.is_helper,
            "hospital_id": u.hospital_id, "centre_id": u.centre_id, **{f: getattr(u, f) for f in PROFILE_FIELDS},
            "location": None if u.last_lat is None else {"lat": u.last_lat, "lon": u.last_lon, "at": clock.iso(u.last_seen_at)}}


def profile_view(u: User | None):
    """The medical profile alone, for a dispatcher with an active incident or a hospital with a patient on the way."""
    if u is None:
        return None
    return {"user_id": u.id, "name": u.name, "phone": u.phone, **{f: getattr(u, f) for f in PROFILE_FIELDS}}


async def create_user(s, phone, password, name, role="citizen", **fields):
    user = User(phone=phone, password_hash=auth.hash_password(password), name=name, role=role,
                **{k: v for k, v in fields.items() if hasattr(User, k) and v is not None})
    s.add(user)
    await s.flush()
    return user


async def find_by_phone(s, phone):
    return await s.scalar(select(User).where(User.phone == phone))


async def update_location(s, user, lat, lon, driving=None):
    user.last_lat, user.last_lon, user.last_seen_at = lat, lon, clock.now()
    if driving is not None:
        user.last_driving = driving
    await s.flush()
    await bus.publish("user.located", s=s, user=user)


async def nearby(s, point, radius_m, exclude=(), role="citizen", helpers_only=False):
    """Users whose last known location is within radius_m of the point and recent, closest first, with their distance."""
    fresh = clock.now() - timedelta(seconds=settings.location_fresh_s)
    q = select(User).where(User.last_lat.is_not(None), User.last_seen_at >= fresh, User.role == role)
    if helpers_only:
        q = q.where(User.is_helper.is_(True))
    found = []
    for u in (await s.scalars(q)).all():
        d = haversine_m(point, (u.last_lat, u.last_lon))
        if d <= radius_m and u.id not in exclude:
            found.append((u, d))
    return sorted(found, key=lambda x: x[1])


async def register_device(s, device_id, owner_id=None, body_type="car"):
    """Returns the key, which is not stored and cannot be shown again."""
    key = auth.new_device_key()
    s.add(Device(id=device_id, key_hash=auth.hash_key(key), owner_id=owner_id, body_type=body_type))
    await s.flush()
    return key
