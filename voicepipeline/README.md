# VoiceAIDoctor

A browser-based doctor appointment assistant built with a Python/Pipecat voice pipeline and a React dashboard. The dashboard and voice tools use the same SQLite database, so mock doctors and appointment slots persist between restarts and bookings immediately update the availability and appointment lists.

## What is included

- ElevenLabs real-time speech recognition and speech generation, Groq tool calling, and Silero voice activity detection in a Pipecat WebRTC pipeline.
- Phone-number confirmation before either voice booking or cancellation.
- A React dashboard for managing doctors, availability, appointments, and call transcripts.
- A live browser call screen with the VoiceAIDoctor logo, opening assistant greeting, microphone controls, and an in-call transcript.
- A SQLite database seeded with two example doctors and four future slots on its first run.

## Run locally

1. Install Python 3.11+ and Node.js 20+.
2. From the repository root, create and activate a virtual environment, then install the backend:

   ```powershell
   cd VOICEPIPELINE
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e .
   ```

3. In the backend terminal, copy `.env.example` to `.env` and add your Groq and ElevenLabs credentials. The dashboard and data-entry tools work without provider keys; voice calls require all three values.
4. Start the API:

   ```powershell
   uvicorn voicepipeline.api:app --reload --app-dir src --port 8001
   ```

5. In another terminal opened at the repository root, start the React dashboard:

   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

6. Open the Vite URL (usually `http://localhost:5173`). Allow microphone access to make a voice call.

The backend listens on port `8001`, matching the dashboard's default API URL. Keep `VOICEPIPELINE` and `frontend` together in the same repository.

## Configuration and data

- `GROQ_API_KEY`: Groq API key. The assistant uses Groq's `openai/gpt-oss-20b` model. Groq lists standard Developer pricing at $0.075 per million input tokens and $0.30 per million output tokens; verify whether your account includes this model in a free quota before demonstrating or submitting. Do not assume usage is free.
- `ELEVENLABS_API_KEY`: ElevenLabs API key used for speech-to-text and speech generation.
- `ELEVENLABS_VOICE_ID`: ElevenLabs voice ID.
- `DATABASE_PATH`: optional path for the SQLite database; defaults to `data/appointments.sqlite3`.
- `FRONTEND_ORIGINS`: optional comma-separated dashboard origins; defaults to the local Vite URLs.
- `LOG_LEVEL`: optional Python logging level; defaults to `INFO`.
- `LOG_FILE`: optional log path; defaults to `logs/voiceaidoctor.log` under `VOICEPIPELINE`. Relative paths resolve from that directory. Logs rotate at 5 MB and retain three backups. Startup, pipeline errors, tool outcomes, and shutdown are logged to both console and file.
- `VITE_API_URL`: optional backend URL for the dashboard; defaults to `http://localhost:8001`.

Doctor names and specialties, availability, bookings, and cancellation history are stored in SQLite. Slots in the past are rejected, booked slots stop appearing as available, and cancelling a booking frees its slot again.
Voice availability checks can optionally filter by a date in `YYYY-MM-DD` format. If a caller has multiple active appointments, the assistant asks which doctor and time they mean before cancelling.

## Voice pipeline

```text
Browser microphone
  -> WebRTC input -> ElevenLabs STT -> caller transcript -> Silero VAD/user turn
  -> Groq LLM + appointment tools -> assistant transcript -> ElevenLabs TTS
  -> WebRTC output -> browser speaker
```

`VoiceSessionManager` owns each pipeline task and cancels outstanding sessions during application shutdown. Voice-tool database work runs outside the event loop so SQLite operations cannot stall audio processing. Transcript WebSocket origins are restricted to `FRONTEND_ORIGINS`, and slow/disconnected transcript clients are removed without serially blocking other listeners. The dashboard reflects actual WebRTC connection states, supports speaker retry/control, and masks caller phone numbers in the appointment list.

The appointment panel defaults to upcoming visits and provides filters for all and cancelled appointments.

## API

- `GET /api/health`, `GET /api/dashboard`
- `GET /api/doctors`, `POST /api/doctors`, `POST /api/doctors/{doctor_id}/slots`
- `GET /api/appointments`, `POST /api/appointments`, `POST /api/appointments/cancel`
- `POST /api/offer` and `PATCH /api/offer` for WebRTC; `/api/transcript/{session_id}` for live transcript events.
# VOICEAIDOCTOR
