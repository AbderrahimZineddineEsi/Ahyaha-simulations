from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from backend import auth, db
from backend.deps import current_user, get_session, require
from backend.models import Device, User
from backend.users import service

router = APIRouter(tags=["accounts"])


class Profile(BaseModel):
    age: int | None = Field(None, ge=0, le=120)
    sex: Literal["male", "female"] | None = None
    blood_type: str | None = Field(None, max_length=5)
    conditions: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    skills: str | None = None
    is_helper: bool | None = None
    name: str | None = Field(None, min_length=1, max_length=120)


class Register(Profile):
    phone: str = Field(min_length=5, max_length=40)
    password: str = Field(min_length=6, max_length=72)
    name: str = Field(min_length=1, max_length=120)

    @field_validator("password")
    @classmethod
    def fits_bcrypt(cls, password):
        if len(password.encode()) > 72:   # bcrypt refuses more, and accented letters take two bytes
            raise ValueError("password is too long")
        return password


class Login(BaseModel):
    phone: str
    password: str


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    driving: bool | None = None


class PushToken(BaseModel):
    token: str = Field(max_length=200)


class NewUser(Register):
    role: Literal["citizen", "dispatcher", "hospital", "admin"] = "citizen"
    hospital_id: int | None = None
    centre_id: int | None = None


class NewDevice(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    body_type: Literal["car", "utility", "van", "pickup"] = "car"
    owner_id: int | None = None


def session_for(user):
    return {"token": auth.make_token(user), "user": service.own_view(user)}


@router.post("/auth/register", status_code=201)
async def register(body: Register, s=Depends(get_session)):
    if await service.find_by_phone(s, body.phone):
        raise HTTPException(409, "this phone number already has an account")
    user = await service.create_user(s, **body.model_dump(exclude_none=True))
    await db.commit(s)
    return session_for(user)


@router.post("/auth/login")
async def login(body: Login, s=Depends(get_session)):
    user = await service.find_by_phone(s, body.phone)
    if user is None or not auth.check_password(body.password, user.password_hash):
        raise HTTPException(401, "wrong phone number or password")
    return session_for(user)


@router.get("/me")
async def me(user=Depends(current_user)):
    return service.own_view(user)


@router.patch("/me")
async def patch_me(body: Profile, user=Depends(current_user), s=Depends(get_session)):
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(user, field, value)
    await db.commit(s)
    return service.own_view(user)


@router.post("/me/location")
async def location(body: Location, user=Depends(current_user), s=Depends(get_session)):
    await service.update_location(s, user, body.lat, body.lon, body.driving)
    await db.commit(s)
    return {"status": "ok"}


@router.post("/me/push-token")
async def push_token(body: PushToken, user=Depends(current_user), s=Depends(get_session)):
    user.push_token = body.token
    await db.commit(s)
    return {"status": "ok"}


@router.post("/devices", status_code=201)
async def add_device(body: NewDevice, user=Depends(current_user), s=Depends(get_session)):
    """Registers a blackbox and returns its key once. Citizens register their own; admins can name the owner."""
    if user.role != "admin" and body.owner_id not in (None, user.id):
        raise HTTPException(403, "you can only register your own blackbox")
    if await s.get(Device, body.device_id):
        raise HTTPException(409, "this device id exists")
    owner = body.owner_id if user.role == "admin" and body.owner_id else user.id
    key = await service.register_device(s, body.device_id, owner, body.body_type)
    await db.commit(s)
    return {"device_id": body.device_id, "device_key": key, "owner_id": owner}


@router.post("/admin/users", status_code=201)
async def admin_create_user(body: NewUser, admin=Depends(require("admin")), s=Depends(get_session)):
    if await service.find_by_phone(s, body.phone):
        raise HTTPException(409, "this phone number already has an account")
    user = await service.create_user(s, **body.model_dump(exclude_none=True))
    await db.commit(s)
    return service.own_view(user)


@router.get("/admin/users")
async def admin_users(admin=Depends(require("admin")), s=Depends(get_session)):
    return [service.own_view(u) for u in (await s.scalars(select(User).order_by(User.id))).all()]
