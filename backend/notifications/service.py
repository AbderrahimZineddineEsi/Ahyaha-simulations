"""
Sending notifications and what happens to them. Channels in order: Socket.IO if the user is connected, then Expo
push if nobody acknowledged it, then an SMS stub that only logs. Actions that wait for an answer carry a deadline
(`due_at`) that the scheduler enforces, so a restart doesn't lose them.
"""
import logging
from datetime import timedelta

import httpx
from sqlalchemy import select

from backend import clock, db, realtime
from backend.config import settings
from backend.models import Notification, User

log = logging.getLogger("solvex")


def view(n: Notification):
    remaining = None if n.due_at is None else max(0.0, (n.due_at - clock.now()).total_seconds())
    return {"id": n.id, "user_id": n.user_id, "incident_id": n.incident_id, "kind": n.kind, "title": n.title, "text": n.text,
            "actions": n.actions, "data": n.data, "state": n.state, "answer": n.answer, "sent_at": clock.iso(n.created_at),
            "remaining_s": remaining}


async def _deliver(n, user):
    if realtime.is_online(n.user_id):
        await realtime.emit_user(n.user_id, "notification", view(n))
    else:
        await push(user, n)


async def notify(s, user_id, kind, title, text, actions=(), deadline_s=None, incident_id=None, data=None):
    n = Notification(user_id=user_id, kind=kind, title=title, text=text, actions=list(actions), data=data or {},
                     state="open" if actions else "delivered", incident_id=incident_id,
                     due_at=clock.in_seconds(deadline_s) if deadline_s else None)
    user = await s.get(User, user_id)
    if realtime.is_online(user_id):
        n.channel = "socket"
    else:       # no socket to wait for: every second counts, so the push goes now
        n.channel, n.push_sent_at = "push" if user.push_token else None, clock.now()
    s.add(n)
    await s.flush()
    db.after_commit(s, _deliver, n, user)
    return n


def changed(s, n):
    """Tell the phone that a notification it holds moved on, so it stops showing the buttons."""
    db.after_commit(s, realtime.emit_user, n.user_id, "notification", view(n))


def mark_acked(n):
    if n.acked_at is None:
        n.acked_at = clock.now()


async def expire_due(s):
    """Open notifications whose deadline passed. The caller decides what that means for each kind."""
    rows = (await s.scalars(select(Notification).where(Notification.state == "open", Notification.due_at <= clock.now()))).all()
    for n in rows:
        n.state = "expired"
        changed(s, n)
    return list(rows)


async def expire_open(s, incident_id, kinds=None, state="expired", except_id=None):
    """Open notifications of an incident that nobody needs answered any more. Returns the ones it closed."""
    q = select(Notification).where(Notification.incident_id == incident_id, Notification.state == "open")
    if kinds:
        q = q.where(Notification.kind.in_(kinds))
    if except_id is not None:
        q = q.where(Notification.id != except_id)
    rows = (await s.scalars(q)).all()
    for n in rows:
        n.state = state
        changed(s, n)
    return rows


async def push(user, n):
    if not user.push_token:
        return
    if not settings.expo_push:
        log.info("[push stub] to user %s: %s. %s", user.id, n.title, n.text)
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post("https://exp.host/--/api/v2/push/send", json={"to": user.push_token, "title": n.title, "body": n.text})
    except Exception as e:
        log.warning("push failed: %s", e)


async def escalate(s):
    """Notifications nobody acknowledged: a push for a connected phone that stayed silent, then an SMS."""
    now = clock.now()
    rows = (await s.scalars(select(Notification).where(Notification.acked_at.is_(None), Notification.state.in_(("open", "delivered")),
                                                        Notification.created_at <= now - timedelta(seconds=settings.push_after_s),
                                                        Notification.sms_sent_at.is_(None)))).all()
    for n in rows:
        user = await s.get(User, n.user_id)
        if n.push_sent_at is None:
            n.push_sent_at, n.channel = now, "push"
            await push(user, n)
        elif now - n.created_at >= timedelta(seconds=settings.sms_after_s):
            n.sms_sent_at, n.channel = now, "sms"
            log.info("[sms stub] to %s: %s", user.phone, n.text)
    return len(rows)
