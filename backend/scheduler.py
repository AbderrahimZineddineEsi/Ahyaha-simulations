"""
Everything that waits for a time: notification deadlines, escalation to push and SMS, the dead-zone watch. Each
deadline is a column in the database, so a restart loses nothing. `tick()` is also what tests call after moving the clock.
"""
import asyncio
import logging

from backend import db
from backend.checkins import service as checkins
from backend.config import settings
from backend.incidents import service as incidents
from backend.notifications import service as notifications

log = logging.getLogger("solvex")


async def tick():
    async with db.session_scope() as s:
        for note in await notifications.expire_due(s):
            await incidents.on_note_expired(s, note)
        await checkins.step(s)
        await notifications.escalate(s)


async def run():
    while True:
        await asyncio.sleep(settings.scheduler_s)
        try:
            await tick()
        except Exception:
            log.exception("the scheduler's tick failed")
