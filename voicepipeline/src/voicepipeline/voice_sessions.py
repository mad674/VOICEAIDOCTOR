import asyncio
import logging
from typing import Any

from voicepipeline.database import Database
from voicepipeline.transcripts import TranscriptManager


logger = logging.getLogger(__name__)


class VoiceSessionManager:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    def start(
        self,
        connection: Any,
        database: Database,
        transcripts: TranscriptManager,
    ) -> asyncio.Task[None]:
        from voicepipeline.bot import run_bot

        session_id = connection.pc_id
        current = self._tasks.get(session_id)
        if current is not None and not current.done():
            return current

        logger.info("Starting voice pipeline session %s", session_id)
        task = asyncio.create_task(
            run_bot(connection, session_id, transcripts, database),
            name=f"voice-pipeline-{session_id}",
        )
        self._tasks[session_id] = task
        task.add_done_callback(lambda completed: self._finish(session_id, completed))
        return task

    async def shutdown(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def _finish(self, session_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(session_id) is task:
            self._tasks.pop(session_id)

        if task.cancelled():
            logger.info("Voice pipeline session %s was cancelled", session_id)
            return

        if error := task.exception():
            logger.error(
                "Voice pipeline session %s stopped unexpectedly",
                session_id,
                exc_info=(type(error), error, error.__traceback__),
            )
        else:
            logger.info("Voice pipeline session %s finished", session_id)
