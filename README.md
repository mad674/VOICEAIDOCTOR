# VoiceAIDoctor

A browser-based appointment assistant built with React, FastAPI, Pipecat, SQLite, ElevenLabs speech services, and Groq. The dashboard and voice tools share the same database.
The dashboard uses an original VoiceAIDoctor SVG logo and favicon.

## Project Demo

Watch the application demo: https://www.youtube.com/watch?v=AWJJMI-ogv0

[![Voice AI Doctor Demo](https://img.youtube.com/vi/AWJJMI-ogv0/0.jpg)](https://www.youtube.com/watch?v=AWJJMI-ogv0)

## Run locally

1. Install Python 3.11+ and Node.js 20+.
2. From the repository root, in a backend terminal:

   ```powershell
   cd VOICEPIPELINE
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e .
   Copy-Item .env.example .env
   ```

   Add `GROQ_API_KEY`, `ELEVENLABS_API_KEY`, and `ELEVENLABS_VOICE_ID` to `VOICEPIPELINE\.env`, then start the API:

   ```powershell
   cd voicepipeline
   uvicorn voicepipeline.api:app --reload --app-dir src --port 8001
   ```

3. In a second terminal opened at the repository root:

   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

4. Open the Vite URL (usually `http://localhost:5173`) and allow microphone access to try a call.

Voice calls require provider credentials; the dashboard can be explored without them. Verify Groq free-tier eligibility and rate limits for your account before using it for a demo.

## Voice flow

When a call connects, the assistant speaks a short greeting before waiting for the caller. Browser audio then travels over WebRTC to Pipecat. ElevenLabs transcribes speech, Silero detects turns, Groq handles the conversation and invokes appointment tools backed by SQLite, and ElevenLabs synthesizes the response audio for WebRTC playback. The phone-style call screen shows the live transcript and microphone waveform, retains an ended-call summary, and displays booking/cancellation confirmations; matching dashboard rows briefly highlight after schedule updates.

See [VOICEPIPELINE/README.md](./VOICEPIPELINE/README.md) for configuration, architecture, API details, and rotating application logs.
See the [architecture diagrams and pipeline description](./.github/modernize/assessment/engines/facts/architecture-diagram.md) for the application, component, class, and call-flow views.

## Booking demo checklist

For the required short demo, show a caller asking for a doctor's availability, confirming a spoken phone number, booking one returned slot, hearing the assistant confirm it, and seeing the appointment appear in the dashboard. Keep the recording under three minutes and use test data.

## Copyright

The original VoiceAIDoctor materials are proprietary. See [LICENSE](./LICENSE); third-party dependencies remain subject to their own licenses. Confirm the copyright holder before public release; this basic notice is not legal advice.
# VOICEAIDOCTOR
