"""
The database tables. Times are naive UTC (see clock.py). JSON columns hold what is shown but never searched.
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from backend import clock

ROLES = ("citizen", "dispatcher", "hospital", "admin")


class Base(DeclarativeBase):
    pass


def now_col():
    return mapped_column(DateTime, default=clock.now)


class Centre(Base):
    __tablename__ = "centres"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float]
    lon: Mapped[float]


class Hospital(Base):
    __tablename__ = "hospitals"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    lat: Mapped[float]
    lon: Mapped[float]
    specialties: Mapped[list] = mapped_column(JSON, default=list)
    beds_total: Mapped[int] = mapped_column(Integer, default=0)
    beds_free: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = now_col()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="citizen")
    phone: Mapped[str] = mapped_column(String(40), unique=True)
    password_hash: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(120))
    age: Mapped[int | None]
    sex: Mapped[str | None] = mapped_column(String(10))            # "male" or "female"
    blood_type: Mapped[str | None] = mapped_column(String(5))
    conditions: Mapped[str | None] = mapped_column(Text)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(120))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(40))
    skills: Mapped[str | None] = mapped_column(String(160))
    is_helper: Mapped[bool] = mapped_column(Boolean, default=False)
    hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id"))
    centre_id: Mapped[int | None] = mapped_column(ForeignKey("centres.id"))
    last_lat: Mapped[float | None]
    last_lon: Mapped[float | None]
    last_driving: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    push_token: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = now_col()


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # what the blackbox sends as device_id
    key_hash: Mapped[str] = mapped_column(String(64))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    body_type: Mapped[str] = mapped_column(String(20), default="car")  # car, utility, van, pickup
    created_at: Mapped[datetime] = now_col()


class Telemetry(Base):
    __tablename__ = "telemetry"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128))
    seq: Mapped[int]
    recorded_at: Mapped[str | None] = mapped_column(String(40))
    lat: Mapped[float | None]
    lon: Mapped[float | None]
    speed_kmh: Mapped[float | None]
    acc: Mapped[bytes] = mapped_column(LargeBinary)    # float32, shape (100, 3)
    gyro: Mapped[bytes] = mapped_column(LargeBinary)
    received_at: Mapped[datetime] = now_col()
    __table_args__ = (Index("telemetry_device", "device_id", "id"),)


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128))
    trigger_seq: Mapped[int | None]
    level: Mapped[str] = mapped_column(String(12))
    p_crash: Mapped[float]
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = now_col()


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), default="crash")        # crash or dead_zone
    status: Mapped[str] = mapped_column(String(20), default="analyzing")
    confidence: Mapped[str | None] = mapped_column(String(8))
    lat: Mapped[float | None]
    lon: Mapped[float | None]
    device_id: Mapped[str | None] = mapped_column(String(128))
    victim_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    trigger_seq: Mapped[int | None]
    peak_g: Mapped[float | None]
    m2_level: Mapped[str | None] = mapped_column(String(10))
    m2_p: Mapped[float | None]
    m3_level: Mapped[str | None] = mapped_column(String(8))
    m3_p: Mapped[float | None]
    severity: Mapped[dict | None] = mapped_column(JSON)   # M3 + M4 result, the input used, and the level shown
    agent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    agent_severity: Mapped[str | None] = mapped_column(String(8))
    user_state: Mapped[str | None] = mapped_column(String(16))
    nobody_around: Mapped[bool] = mapped_column(Boolean, default=False)
    help_radius_km: Mapped[int | None]
    flags: Mapped[dict] = mapped_column(JSON, default=dict)               # unknown_victim, default_profile, reports ...
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now, onupdate=clock.now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("incidents_device", "device_id", "trigger_seq"),)

    victim: Mapped["User | None"] = relationship(foreign_keys=[victim_id], lazy="joined")


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    at: Mapped[datetime] = now_col()
    audience: Mapped[str] = mapped_column(String(8), default="agent")   # agent, or dev for the simulation's own log
    kind: Mapped[str] = mapped_column(String(16), default="info")
    text: Mapped[str] = mapped_column(Text)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(160))
    text: Mapped[str] = mapped_column(Text)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(10), default="delivered")  # open, answered, expired, withdrawn (no longer needed), delivered
    answer: Mapped[str | None] = mapped_column(String(24))
    created_at: Mapped[datetime] = now_col()
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime)
    channel: Mapped[str | None] = mapped_column(String(8))   # the last one used: socket, push, sms
    acked_at: Mapped[datetime | None] = mapped_column(DateTime)
    push_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    sms_sent_at: Mapped[datetime | None] = mapped_column(DateTime)


class Ambulance(Base):
    __tablename__ = "ambulances"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    centre_id: Mapped[int] = mapped_column(ForeignKey("centres.id"))
    name: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(12), default="available")  # available, busy, dispatched, on_scene
    prep_s: Mapped[int] = mapped_column(Integer, default=0)             # the crew's time to get ready
    note: Mapped[str | None] = mapped_column(String(160))
    lat: Mapped[float | None]
    lon: Mapped[float | None]
    located_at: Mapped[datetime | None] = mapped_column(DateTime)


class Dispatch(Base):
    __tablename__ = "dispatches"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    ambulance_id: Mapped[str] = mapped_column(ForeignKey("ambulances.id"))
    state: Mapped[str] = mapped_column(String(10), default="en_route")   # en_route, on_scene, returned
    eta_s: Mapped[int]
    start_m: Mapped[float] = mapped_column(Float, default=0)             # straight distance to the target when it left
    target_lat: Mapped[float]
    target_lon: Mapped[float]
    route: Mapped[list | None] = mapped_column(JSON)
    dispatched_at: Mapped[datetime] = now_col()
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime)


class HelperRequest(Base):
    __tablename__ = "helper_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    state: Mapped[str] = mapped_column(String(12), default="requested")  # requested, accepted, declined, confirmed, on_scene, cancelled
    distance_m: Mapped[float]
    eta_s: Mapped[int | None]
    route: Mapped[list | None] = mapped_column(JSON)
    start_lat: Mapped[float | None]
    start_lon: Mapped[float | None]
    requested_at: Mapped[datetime] = now_col()
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime)

    user: Mapped[User] = relationship(lazy="joined")


class DeadZoneCheckin(Base):
    __tablename__ = "dead_zone_checkins"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"))
    state: Mapped[str] = mapped_column(String(12), default="driving")    # driving, in_zone, overdue, found, returned, turned_back, closed
    road: Mapped[str | None] = mapped_column(String(120))
    zone: Mapped[dict] = mapped_column(JSON)                             # {"center": [lat, lon], "radius_m": n}
    entry_lat: Mapped[float]
    entry_lon: Mapped[float]
    back_by: Mapped[datetime]                                            # what the driver said
    overdue_at: Mapped[datetime]                                         # when the server raises the alarm
    defaulted: Mapped[bool] = mapped_column(Boolean, default=False)
    last_lat: Mapped[float | None]
    last_lon: Mapped[float | None]
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    warned: Mapped[bool] = mapped_column(Boolean, default=False)
    told_no_help: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = now_col()


class PatientTransfer(Base):
    __tablename__ = "patient_transfers"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    state: Mapped[str] = mapped_column(String(12), default="incoming")   # incoming, arrived, cancelled
    eta_s: Mapped[int | None]
    created_at: Mapped[datetime] = now_col()
