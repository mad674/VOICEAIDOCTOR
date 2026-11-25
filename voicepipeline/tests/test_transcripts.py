import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from voicepipeline.transcripts import TranscriptManager


class TranscriptManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_removes_timed_out_client_without_blocking_others(self):
        responsive_client = Mock()
        responsive_client.accept = AsyncMock()
        responsive_client.send_json = AsyncMock()
        stalled_client = Mock()
        stalled_client.accept = AsyncMock()
        stalled_client.send_json = AsyncMock(side_effect=asyncio.TimeoutError)
        manager = TranscriptManager()

        await manager.connect("session-1", responsive_client)
        await manager.connect("session-1", stalled_client)
        await manager.publish("session-1", "assistant", "Hello.")

        responsive_client.send_json.assert_awaited_once_with(
            {"speaker": "assistant", "text": "Hello."}
        )
        await manager.publish("session-1", "assistant", "Still there?")
        self.assertEqual(responsive_client.send_json.await_count, 2)
        stalled_client.send_json.assert_awaited_once()

    async def test_publish_includes_optional_appointment_event_data(self):
        client = Mock()
        client.accept = AsyncMock()
        client.send_json = AsyncMock()
        manager = TranscriptManager()
        event = {
            "type": "appointment_booked",
            "appointment_id": "appointment-1",
            "doctor": "Dr. Maya Sharma",
            "starts_at": "2026-11-25T10:00+00:00",
        }

        await manager.connect("session-1", client)
        await manager.publish("session-1", "system", "", event_data=event)

        client.send_json.assert_awaited_once_with(
            {"speaker": "system", "text": "", "event": event}
        )


if __name__ == "__main__":
    unittest.main()
