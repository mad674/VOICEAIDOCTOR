import asyncio
import logging

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect
from pipecat.frames.frames import TextFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class TranscriptManager:
    def __init__(self) -> None:
        self._clients: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.setdefault(session_id, set()).add(websocket)

    async def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            clients = self._clients.get(session_id)
            if clients is not None:
                clients.discard(websocket)
                if not clients:
                    self._clients.pop(session_id, None)

    async def publish(
        self,
        session_id: str,
        speaker: str,
        text: str,
        event_data: dict[str, str] | None = None,
    ) -> None:
        message: dict[str, object] = {"speaker": speaker, "text": text}
        if event_data is not None:
            message["event"] = event_data
        async with self._lock:
            clients = tuple(self._clients.get(session_id, ()))

        async def send(client: WebSocket) -> WebSocket | None:
            try:
                await asyncio.wait_for(client.send_json(message), timeout=1)
            except (WebSocketDisconnect, RuntimeError) as error:
                logging.getLogger(__name__).debug("Removing disconnected transcript client: %s", error)
                return client
            except asyncio.TimeoutError:
                logging.getLogger(__name__).debug("Removing timed-out transcript client")
                return client
            return None

        failed_clients = await asyncio.gather(*(send(client) for client in clients))
        for client in failed_clients:
            if client is not None:
                await self.disconnect(session_id, client)


class TranscriptProcessor(FrameProcessor):
    def __init__(self, session_id: str, manager: TranscriptManager) -> None:
        super().__init__()
        self._session_id = session_id
        self._manager = manager

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            await self._manager.publish(self._session_id, "caller", frame.text.strip())
        elif isinstance(frame, TextFrame) and direction == FrameDirection.DOWNSTREAM:
            text = frame.text.strip()
            if text:
                await self._manager.publish(self._session_id, "assistant", text)
        await self.push_frame(frame, direction)
