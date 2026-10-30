"""
A tiny in-process event bus. Services publish what happened; the sim subscribes to react like a client would.
Nothing in the services depends on who listens.
"""
import asyncio
import logging
from collections import defaultdict

log = logging.getLogger("solvex")
_handlers = defaultdict(list)


def subscribe(name, handler):
    _handlers[name].append(handler)


def clear():
    _handlers.clear()


async def publish(name, **data):
    for handler in list(_handlers[name]):
        try:
            result = handler(**data)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            log.exception("handler of %s failed", name)
