# Parity checks

Scripts that drive a **running** demo server through its real API and pages, the way the old `server/` was checked by hand. They aren't part of `pytest backend/tests` because they need a server, a browser, and about ten minutes.

```bash
python -m backend.main --port 8002 &                       # a copy with its own database: DATABASE_URL=sqlite+aiosqlite:///... 
export SOLVEX_URL=http://localhost:8002
python -m backend.tests.parity.scenarios all               # A, B, 2, 3 (turned back and not), both SOS buttons, /dev/run
python -m backend.tests.parity.telemetry_ws                # the raw telemetry WebSocket, as the friend's service uses it
pip install playwright && playwright install chromium
python -m backend.tests.parity.ui                          # dashboard + /dev in Chromium: all four scenarios, Auto, SOS, turned back, a false alarm, two incidents, no outside requests
```

Expected, from the old demo: A is medium (3.2%), ETA about 7 min. B is extreme (69%), ETA 18 min, the helper arrives first. Scenario 2 is high (41.7%) with nobody nearby and ETA about 23 min. Scenario 3 ends with "Found: it was a crash".
