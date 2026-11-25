import logging
import os
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from voicepipeline.database import (
    AmbiguousAppointmentError,
    Database,
    NotFoundError,
    PhoneConfirmationError,
    SlotUnavailableError,
)
from voicepipeline.transcripts import TranscriptManager
from voicepipeline.voice_sessions import VoiceSessionManager

load_dotenv()
PROJECT_DIR = Path(__file__).resolve().parents[2]
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_LEVEL = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=LOG_LEVEL,
    format=LOG_FORMAT,
)
log_file_setting = os.environ.get("LOG_FILE", "").strip()
log_file = Path(log_file_setting) if log_file_setting else Path("logs") / "voiceaidoctor.log"
if not log_file.is_absolute():
    log_file = PROJECT_DIR / log_file
log_file.parent.mkdir(parents=True, exist_ok=True)
root_logger = logging.getLogger()
root_logger.setLevel(LOG_LEVEL)
resolved_log_file = log_file.resolve()
if not any(
    isinstance(handler, RotatingFileHandler)
    and Path(handler.baseFilename).resolve() == resolved_log_file
    for handler in root_logger.handlers
):
    file_handler = RotatingFileHandler(
        resolved_log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    file_handler.setLevel(LOG_LEVEL)
    root_logger.addHandler(file_handler)
logger = logging.getLogger(__name__)


class DoctorInput(BaseModel):
    name: str = Field(min_length=3, max_length=100)
    specialty: str = Field(min_length=2, max_length=100)

    @field_validator("name", "specialty", mode="before")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class SlotInput(BaseModel):
    starts_at: datetime
    duration_minutes: int = Field(default=30, ge=10, le=240)


class AppointmentInput(BaseModel):
    phone_number: str = Field(min_length=7, max_length=20)
    doctor_id: str
    slot_id: str
    phone_confirmed: bool = False


class CancelInput(BaseModel):
    phone_number: str = Field(min_length=7, max_length=20)
    appointment_id: str | None = None


class OfferInput(BaseModel):
    sdp: str
    type: str
    pc_id: str | None = None
    restart_pc: bool | None = None
    request_data: Any | None = None


class CandidateInput(BaseModel):
    candidate: str
    sdp_mid: str
    sdp_mline_index: int


class PatchInput(BaseModel):
    pc_id: str
    candidates: list[CandidateInput]


def create_app(database: Database | None = None) -> FastAPI:
    store = database or Database(
        os.environ.get(
            "DATABASE_PATH",
            str(PROJECT_DIR / "data" / "appointments.sqlite3"),
        )
    )
    transcripts = TranscriptManager()
    peer_handler = SmallWebRTCRequestHandler()
    voice_sessions = VoiceSessionManager()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        store.initialize()
        try:
            yield
        finally:
            try:
                await peer_handler.close()
            finally:
                await voice_sessions.shutdown()

    application = FastAPI(title="VoiceAIDoctor API", lifespan=lifespan)
    application.state.database = store
    application.state.peer_handler = peer_handler
    application.state.voice_sessions = voice_sessions
    origins = [
        origin.strip()
        for origin in os.environ.get(
            "FRONTEND_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    ]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/api/health")
    def health() -> dict:
        required = ("GROQ_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")
        missing = [key for key in required if not os.getenv(key)]
        return {"status": "ok", "voice_ready": not missing, "missing_voice_settings": missing}

    @application.get("/api/dashboard")
    def dashboard() -> dict:
        return {"doctors": store.list_doctors(), "appointments": store.list_appointments()}

    @application.get("/api/doctors")
    def list_doctors() -> list[dict]:
        return store.list_doctors()

    @application.post("/api/doctors", status_code=201)
    def create_doctor(body: DoctorInput) -> dict:
        try:
            return store.create_doctor(body.name, body.specialty)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/doctors/{doctor_id}/slots", status_code=201)
    def create_slot(doctor_id: str, body: SlotInput) -> dict:
        try:
            return store.create_slot(doctor_id, body.starts_at, body.duration_minutes)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.get("/api/appointments")
    def list_appointments() -> list[dict]:
        return store.list_appointments()

    @application.post("/api/appointments", status_code=201)
    def create_appointment(body: AppointmentInput) -> dict:
        try:
            return store.book_appointment(
                body.phone_number,
                body.doctor_id,
                body.slot_id,
                body.phone_confirmed,
            )
        except PhoneConfirmationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SlotUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.post("/api/appointments/cancel")
    def cancel_appointment(body: CancelInput) -> dict:
        try:
            return store.cancel_appointment(body.phone_number, body.appointment_id)
        except AmbiguousAppointmentError as exc:
            raise HTTPException(
                status_code=409,
                detail="More than one active appointment matches this phone number. Select an appointment.",
            ) from exc
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.post("/api/offer")
    async def offer(body: OfferInput) -> dict:
        if not health()["voice_ready"]:
            raise HTTPException(
                status_code=503,
                detail="Add the required voice provider keys to the backend .env file before starting a call.",
            )
        async def start_bot(connection) -> None:
            voice_sessions.start(connection, store, transcripts)

        result = await peer_handler.handle_web_request(
            SmallWebRTCRequest(**body.model_dump(exclude_none=True)),
            webrtc_connection_callback=start_bot,
        )
        if result is None:
            raise HTTPException(status_code=500, detail="The voice call did not return a WebRTC answer.")
        logger.info("WebRTC offer accepted for voice session %s", result.get("pc_id", "unknown"))
        return result

    @application.patch("/api/offer")
    async def patch_offer(body: PatchInput) -> dict:
        request = SmallWebRTCPatchRequest(
            pc_id=body.pc_id,
            candidates=[
                IceCandidate(
                    candidate=candidate.candidate,
                    sdp_mid=candidate.sdp_mid,
                    sdp_mline_index=candidate.sdp_mline_index,
                )
                for candidate in body.candidates
            ],
        )
        await peer_handler.handle_patch_request(request)
        return {"status": "success"}

    @application.websocket("/api/transcript/{session_id}")
    async def transcript(websocket: WebSocket, session_id: str) -> None:
        origin = websocket.headers.get("origin")
        if origin and origin not in origins:
            await websocket.close(code=1008, reason="Origin is not allowed.")
            return
        await transcripts.connect(session_id, websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await transcripts.disconnect(session_id, websocket)

    return application


app = create_app()
