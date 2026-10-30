"""
Settings, read from the environment once at import. Tests and the demo change attributes on `settings` directly.
"""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _flag(name, default):
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "")


@dataclass
class Settings:
    database_url: str = os.environ.get("DATABASE_URL", f"sqlite+aiosqlite:///{Path(__file__).resolve().parent / 'solvex.db'}")
    jwt_secret: str = os.environ.get("JWT_SECRET", "dev-secret-change-me-dev-secret-change-me")
    jwt_hours: int = int(os.environ.get("JWT_HOURS", "24"))
    bcrypt_rounds: int = int(os.environ.get("BCRYPT_ROUNDS", "12"))
    demo: bool = _flag("SOLVEX_DEMO", "1")  # with 0, /dev, /blackbox and the seed data don't exist
    allow_unknown_devices: bool = _flag("ALLOW_UNKNOWN_DEVICES", "1")

    # Incident rules
    checkin_s: int = 30              # how long the driver has to answer "Are you OK?" (the sim shortens it)
    witness_radius_m: int = 2500     # people this close are asked to confirm the crash
    location_fresh_s: int = 900      # a user's last location counts as "nearby" only if this recent
    helper_ranges_km: tuple = (5, 20)
    yellow_line_m: int = 300         # a driver this close to the ambulance is told to clear the way
    arrive_m: int = 60               # a responder this close to the crash is on the scene
    merge_radius_m: int = 300        # reports this close, within the window, are one incident
    merge_window_s: int = 300
    zone_silence_s: int = 10         # a driver silent this long inside a no-coverage zone is in it
    traffic_factor: float = 1.0      # multiplies every route's duration

    # Delivery channels: the socket; push at once if the user isn't connected, or after this long with no acknowledgement;
    # then an SMS stub. Both must come well before checkin_s, or the check-in ends before it reaches anyone.
    push_after_s: int = 5
    sms_after_s: int = 15
    expo_push: bool = _flag("EXPO_PUSH", "0")  # real pushes need an Expo project token

    # Routing: providers registered by the sim, then OSRM, then a straight line. Off in the demo, which has to work offline.
    # The public server is for testing only: production needs its own (OSRM_URL).
    osrm_url: str = os.environ.get("OSRM_URL", "https://router.project-osrm.org")
    osrm_enabled: bool = _flag("OSRM", "0" if _flag("SOLVEX_DEMO", "1") else "1")
    straight_speed_kmh: float = 50.0

    scheduler_s: float = 0.5
    dead_zone_default: str = "eta30"


settings = Settings()
