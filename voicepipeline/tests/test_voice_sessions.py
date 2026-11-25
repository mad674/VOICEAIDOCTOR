import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from voicepipeline.database import Database
from voicepipeline.transcripts import TranscriptManager
from voicepipeline.voice_sessions import VoiceSessionManager


class VoiceSessionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_session_is_removed_from_registry(self):
        connection = SimpleNamespace(pc_id="session-1")
        database = Database(":memory:")
        transcripts = TranscriptManager()
        manager = VoiceSessionManager()

        with patch("voicepipeline.bot.run_bot", new=AsyncMock()) as run_bot:
            task = manager.start(connection, database, transcripts)
            await task
            await asyncio.sleep(0)

        run_bot.assert_awaited_once_with(connection, "session-1", transcripts, database)
        self.assertEqual(manager.active_count, 0)

    async def test_shutdown_cancels_active_sessions(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()
        connection = SimpleNamespace(pc_id="session-2")

        async def wait_for_shutdown(*_args):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        manager = VoiceSessionManager()
        with patch("voicepipeline.bot.run_bot", new=AsyncMock(side_effect=wait_for_shutdown)):
            manager.start(connection, Database(":memory:"), TranscriptManager())
            await started.wait()
            await manager.shutdown()

        self.assertTrue(cancelled.is_set())
        self.assertEqual(manager.active_count, 0)


if __name__ == "__main__":
    unittest.main()
