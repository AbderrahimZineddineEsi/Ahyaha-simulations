"""
SQLite storage. Every second of telemetry goes in `telemetry`, every M2 result in `predictions`.

acc and gyro are stored as raw float32 bytes: np.frombuffer(row["acc"], np.float32).reshape(-1, 3)
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "solvex.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY,
    device_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    recorded_at TEXT,
    lat REAL,
    lon REAL,
    speed_kmh REAL,
    acc BLOB NOT NULL,
    gyro BLOB NOT NULL,
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS telemetry_device ON telemetry (device_id, id);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY,
    device_id TEXT NOT NULL,
    trigger_seq INTEGER,
    level TEXT NOT NULL,
    p_crash REAL NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


class Database:
    def __init__(self, path=DB_PATH):
        self.db = sqlite3.connect(path)
        self.db.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;" + SCHEMA)

    def save_telemetry(self, t, acc, gyro):
        lat, lon = (t.location.lat, t.location.lon) if t.location else (None, None)
        recorded_at = t.recorded_at.isoformat() if t.recorded_at else None
        with self.db:
            self.db.execute(
                "INSERT INTO telemetry (device_id, seq, recorded_at, lat, lon, speed_kmh, acc, gyro)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (t.device_id, t.seq, recorded_at, lat, lon, t.gps_speed_kmh, acc.tobytes(), gyro.tobytes()))

    def save_prediction(self, reply):
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO predictions (device_id, trigger_seq, level, p_crash, result) VALUES (?, ?, ?, ?, ?)",
                (reply["device_id"], reply["trigger_seq"], reply["level"], reply["p_crash"], json.dumps(reply)))
        return cursor.lastrowid
