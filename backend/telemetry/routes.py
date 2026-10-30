import logging

from fastapi import APIRouter, WebSocket

from backend.telemetry.service import service

log = logging.getLogger("solvex")
router = APIRouter()


@router.websocket("/ws/telemetry")
async def telemetry(ws: WebSocket):
    await ws.accept()
    peer = f"{ws.client.host}:{ws.client.port}"
    log.info("%s connected", peer)
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            break
        raw = message.get("text") or message.get("bytes") or ""
        try:
            await service.handle(raw, ws)
        except Exception:
            log.exception("failed on a message from %s", peer)
            await ws.send_json({"type": "error", "code": "internal_error", "device_id": None, "seq": None,
                                "message": "the server failed on this message, see its log"})
    log.info("%s disconnected", peer)
    await service.disconnected(ws)
