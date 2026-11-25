import asyncio
import logging
import os
from datetime import date, datetime
from typing import Protocol

from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import Frame, TextFrame, TTSSpeakFrame, TranscriptionFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from voicepipeline.database import (
    AmbiguousAppointmentError,
    Database,
    NotFoundError,
    SlotUnavailableError,
)
from voicepipeline.transcripts import TranscriptManager


logger = logging.getLogger(__name__)


class FrameQueue(Protocol):
    async def queue_frame(self, frame: Frame) -> None: ...


SYSTEM_PROMPT = """
You are a warm, concise medical receptionist. Help callers check available doctors and
appointment slots, book a selected appointment, and cancel an existing appointment.
Use the tools to retrieve real-time clinic data. Never invent doctors, specialties, slots, or bookings.
Identify a caller only by the phone number they say aloud; there is no caller ID, account, or OTP.
Before booking or cancelling, read the complete phone number back and ask the caller to confirm it.
Wait for an explicit yes before using either tool, and pass phone_confirmed=true only after that yes.
If the caller has not confirmed, ask them first. When checking availability for a date, pass that date in
YYYY-MM-DD format. If more than one appointment matches a cancellation, ask which doctor and date/time they
mean; never choose one on the caller's behalf. A spoken greeting is played automatically when the call
connects, so do not greet the caller a second time. Keep spoken responses to one or two natural sentences."""

OPENING_GREETING = (
    "Hello, thank you for calling VoiceAIDoctor. I'm your appointment assistant. "
    "How can I help you today?"
)


async def queue_opening_greeting(frame_queue: FrameQueue) -> None:
    await frame_queue.queue_frame(TTSSpeakFrame(OPENING_GREETING))


class TranscriptObserver(FrameProcessor):
    def __init__(self, session_id: str, transcripts: TranscriptManager, speaker: str) -> None:
        super().__init__()
        self._session_id = session_id
        self._transcripts = transcripts
        self._speaker = speaker

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if self._speaker == "caller" and isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if text:
                await self._transcripts.publish(self._session_id, self._speaker, text)
        elif self._speaker == "assistant" and isinstance(frame, TextFrame):
            text = frame.text.strip()
            if text:
                await self._transcripts.publish(self._session_id, self._speaker, text)
        await self.push_frame(frame, direction)


def create_tools(
    database: Database,
    transcripts: TranscriptManager | None = None,
    session_id: str | None = None,
) -> tuple[ToolsSchema, dict]:
    async def publish_appointment_update(event_data: dict[str, str]) -> None:
        if transcripts is not None and session_id is not None:
            await transcripts.publish(session_id, "system", "", event_data=event_data)

    async def check_doctor_availability(params) -> None:
        requested_name = params.arguments.get("doctor_name", "").strip().casefold()
        logger.info("Checking doctor availability for %s", requested_name or "unspecified doctor")
        doctors = await asyncio.to_thread(database.list_doctors)
        doctor = next(
            (item for item in doctors if requested_name and requested_name in item["name"].casefold()),
            None,
        )
        if doctor is None:
            logger.warning("Availability check could not find a matching doctor")
            await params.result_callback(
                {"error": "Doctor not found.", "available_doctors": [item["name"] for item in doctors]}
            )
            return
        requested_date = params.arguments.get("date")
        if requested_date:
            try:
                target_date = date.fromisoformat(requested_date)
                if target_date.isoformat() != requested_date:
                    raise ValueError
            except (TypeError, ValueError):
                await params.result_callback({"error": "The requested date must use YYYY-MM-DD format."})
                return
            available_slots = [
                slot
                for slot in doctor["available_slots"]
                if datetime.fromisoformat(slot["starts_at"]).date() == target_date
            ]
        else:
            available_slots = doctor["available_slots"]
        await params.result_callback(
            {
                "doctor": doctor["name"],
                "specialty": doctor["specialty"],
                "available_slots": available_slots,
            }
        )

    async def book_appointment(params) -> None:
        if not params.arguments.get("phone_confirmed", False):
            logger.warning("Booking declined because caller phone confirmation was not provided")
            await params.result_callback(
                {"error": "Read the phone number back and get the caller's explicit confirmation before booking."}
            )
            return
        phone_number = params.arguments.get("phone_number", "").strip()
        requested_name = params.arguments.get("doctor_name", "").strip().casefold()
        slot_id = params.arguments.get("slot_id", "").strip()
        doctors = await asyncio.to_thread(database.list_doctors)
        doctor = next(
            (item for item in doctors if requested_name and requested_name in item["name"].casefold()),
            None,
        )
        if doctor is None:
            logger.warning("Booking failed because the requested doctor was not found")
            await params.result_callback(
                {"error": "Doctor not found.", "available_doctors": [item["name"] for item in doctors]}
            )
            return
        try:
            appointment = await asyncio.to_thread(
                database.book_appointment, phone_number, doctor["id"], slot_id, True
            )
        except (NotFoundError, SlotUnavailableError, ValueError) as error:
            logger.warning("Booking failed for doctor %s and slot %s: %s", doctor["id"], slot_id, error)
            await params.result_callback({"error": str(error)})
            return
        await publish_appointment_update(
            {
                "type": "appointment_booked",
                "appointment_id": appointment["id"],
                "doctor": appointment["doctor_name"],
                "starts_at": appointment["starts_at"],
            }
        )
        await params.result_callback(
            {
                "status": "booked",
                "doctor": appointment["doctor_name"],
                "time": appointment["starts_at"],
                "phone": appointment["phone_number"],
            }
        )
        logger.info("Appointment %s was booked for doctor %s", appointment["id"], doctor["id"])

    async def cancel_appointment(params) -> None:
        if not params.arguments.get("phone_confirmed", False):
            logger.warning("Cancellation declined because caller phone confirmation was not provided")
            await params.result_callback(
                {"error": "Read the phone number back and get the caller's explicit confirmation before cancelling."}
            )
            return
        try:
            appointment = await asyncio.to_thread(
                database.cancel_appointment,
                params.arguments.get("phone_number", ""),
                params.arguments.get("appointment_id"),
            )
        except AmbiguousAppointmentError as error:
            await params.result_callback(
                {
                    "status": "selection_required",
                    "message": (
                        "Ask which appointment to cancel using the doctor and date/time, "
                        "then retry with the selected appointment_id."
                    ),
                    "appointments": [
                        {
                            "appointment_id": item["id"],
                            "doctor": item["doctor_name"],
                            "time": item["starts_at"],
                        }
                        for item in error.appointments
                    ],
                }
            )
            return
        except (NotFoundError, ValueError) as error:
            logger.warning("Appointment cancellation failed: %s", error)
            await params.result_callback({"error": str(error)})
            return
        await publish_appointment_update(
            {
                "type": "appointment_cancelled",
                "appointment_id": appointment["id"],
                "doctor": appointment["doctor_name"],
                "starts_at": appointment["starts_at"],
            }
        )
        await params.result_callback(
            {
                "status": "cancelled",
                "doctor": appointment["doctor_name"],
                "time": appointment["starts_at"],
            }
        )
        logger.info("Appointment %s was cancelled", appointment["id"])

    check_schema = FunctionSchema(
        name="check_doctor_availability",
        description=(
            "Find a doctor's current appointment slots before offering a time. Optionally filter by "
            "calendar date in YYYY-MM-DD format."
        ),
        properties={
            "doctor_name": {
                "type": "string",
                "description": "The doctor's full or distinctive name.",
            },
            "date": {
                "type": "string",
                "description": "Optional date filter in YYYY-MM-DD format.",
            },
        },
        required=["doctor_name"],
    )
    book_schema = FunctionSchema(
        name="book_appointment",
        description=(
            "Book a free slot only after reading the caller's phone number back and receiving "
            "their explicit confirmation."
        ),
        properties={
            "phone_number": {
                "type": "string",
                "description": "The caller's spoken phone number, confirmed aloud by the caller.",
            },
            "phone_confirmed": {
                "type": "boolean",
                "description": "True only after the caller explicitly confirms the repeated phone number.",
            },
            "doctor_name": {"type": "string", "description": "The selected doctor's name."},
            "slot_id": {"type": "string", "description": "The selected available slot ID."},
        },
        required=["phone_number", "phone_confirmed", "doctor_name", "slot_id"],
    )
    cancel_schema = FunctionSchema(
        name="cancel_appointment",
        description=(
            "Cancel an existing appointment only after reading the caller's phone number back "
            "and receiving their explicit confirmation."
        ),
        properties={
            "phone_number": {
                "type": "string",
                "description": "The caller's spoken phone number, confirmed aloud by the caller.",
            },
            "appointment_id": {
                "type": "string",
                "description": "The selected appointment ID when multiple active appointments were returned.",
            },
            "phone_confirmed": {
                "type": "boolean",
                "description": "True only after the caller explicitly confirms the repeated phone number.",
            },
        },
        required=["phone_number", "phone_confirmed"],
    )
    return (
        ToolsSchema(standard_tools=[check_schema, book_schema, cancel_schema]),
        {
            "check_doctor_availability": check_doctor_availability,
            "book_appointment": book_appointment,
            "cancel_appointment": cancel_appointment,
        },
    )


async def run_bot(connection, session_id: str, transcripts: TranscriptManager, database: Database) -> None:
    logger.info("Building voice pipeline for session %s", session_id)
    required = ("GROQ_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        logger.error("Voice pipeline configuration is missing: %s", ", ".join(missing))
        raise RuntimeError(f"Voice services are not configured: {', '.join(missing)}")

    transport = SmallWebRTCTransport(
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
        webrtc_connection=connection,
    )
    stt = ElevenLabsRealtimeSTTService(api_key=os.environ["ELEVENLABS_API_KEY"])
    tts = ElevenLabsTTSService(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        voice_id=os.environ["ELEVENLABS_VOICE_ID"],
    )
    llm = GroqLLMService(
        api_key=os.environ["GROQ_API_KEY"],
        settings=GroqLLMService.Settings(
            model="openai/gpt-oss-20b",
            reasoning_effort="low",
            system_instruction=SYSTEM_PROMPT,
        ),
    )
    schemas, handlers = create_tools(database, transcripts, session_id)
    llm.register_function("check_doctor_availability", handlers["check_doctor_availability"])
    llm.register_function("book_appointment", handlers["book_appointment"])
    llm.register_function("cancel_appointment", handlers["cancel_appointment"])

    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(_service, function_calls) -> None:
        if function_calls:
            await tts.queue_frame(TTSSpeakFrame("Let me check that for you."))

    context = LLMContext(tools=schemas)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.4)),
        ),
    )
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            TranscriptObserver(session_id, transcripts, "caller"),
            user_aggregator,
            llm,
            TranscriptObserver(session_id, transcripts, "assistant"),
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )
    worker = PipelineWorker(pipeline)

    @worker.event_handler("on_pipeline_started")
    async def on_pipeline_started(_worker, _frame) -> None:
        logger.info("Voice pipeline session %s is ready", session_id)
        await queue_opening_greeting(tts)

    @worker.event_handler("on_pipeline_error")
    async def on_pipeline_error(_worker, frame) -> None:
        failed_processor = getattr(frame, "processor", None)
        processor = getattr(failed_processor, "name", type(failed_processor).__name__)
        logger.error(
            "Voice pipeline session %s received an error from %s: %s",
            session_id,
            processor,
            frame.error,
        )

    @worker.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(_worker, _frame) -> None:
        logger.info("Voice pipeline session %s has finished", session_id)

    runner = WorkerRunner(handle_sigint=False)
    try:
        await runner.add_workers(worker)
        await runner.run()
    except Exception:
        logger.exception("Voice pipeline session %s failed", session_id)
        raise
