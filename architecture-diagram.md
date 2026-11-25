# Architecture Diagram

VoiceAIDoctor is a React dashboard backed by a FastAPI service and a Pipecat WebRTC voice pipeline. This document maps its application layers, component and class relationships, call flow, and speech-processing path.

## Application Architecture

<!-- mermaid-checked: no \n, no em-dash/en-dash, no {} in labels, subgraphs are id["label"], arrows are -->|"label"|, all subgraphs closed by end, ids unique -->
```mermaid
flowchart TD
    subgraph browserLayer["Browser Layer"]
        aCaller["Caller"]
        aDashboard["React dashboard"]
        aCall["Call UI and Web Audio"]
    end
    subgraph apiLayer["API and Session Layer"]
        aApi["FastAPI routes"]
        aWebRtcHandler["SmallWebRTC request handler"]
        aSessions["VoiceSessionManager"]
        aTranscriptManager["TranscriptManager"]
        aDatabase["Database adapter"]
    end
    subgraph voiceLayer["Pipecat Voice Runtime"]
        aPipeline["Pipecat pipeline worker"]
        aTransport["SmallWebRTC transport"]
        aStt["ElevenLabs realtime STT"]
        aCallerObserver["Caller transcript observer"]
        aVad["Silero VAD turn analyzer"]
        aLlm["Groq LLM service"]
        aTools["Appointment tools"]
        aAssistantObserver["Assistant transcript observer"]
        aTts["ElevenLabs TTS"]
    end
    subgraph dataLayer["Persistent Data"]
        aSqlite[("SQLite database")]
    end
    subgraph providerLayer["External Services"]
        aGroqApi["Groq API"]
        aElevenLabsApi["ElevenLabs API"]
    end

    aCaller -->|"speaks and hears audio"| aCall
    aDashboard -->|"renders call controls and schedule"| aCall
    aCall -->|"REST requests and WebRTC offer"| aApi
    aCall -->|"audio stream"| aTransport
    aCall -->|"transcript WebSocket"| aTranscriptManager
    aApi -->|"CRUD operations"| aDatabase
    aApi -->|"offer and ICE handling"| aWebRtcHandler
    aWebRtcHandler -->|"connection callback"| aSessions
    aSessions -->|"starts asynchronous task"| aPipeline
    aPipeline -->|"runs call input and output"| aTransport
    aTransport -->|"incoming audio"| aStt
    aStt -->|"transcription frames"| aCallerObserver
    aCallerObserver -->|"caller transcript"| aTranscriptManager
    aCallerObserver -->|"forwards caller frames to user aggregator"| aVad
    aVad -->|"user turn and context"| aLlm
    aLlm -->|"function calls"| aTools
    aTools -->|"appointment reads and writes"| aDatabase
    aLlm -->|"assistant text"| aAssistantObserver
    aAssistantObserver -->|"assistant transcript"| aTranscriptManager
    aLlm -->|"response text"| aTts
    aTts -->|"synthesized audio"| aTransport
    aTransport -->|"assistant audio"| aCall
    aStt -->|"speech recognition"| aElevenLabsApi
    aTts -->|"speech synthesis"| aElevenLabsApi
    aLlm -->|"model inference"| aGroqApi
    aTools -->|"booking and cancellation events"| aTranscriptManager
    aTranscriptManager -->|"transcript and appointment events"| aCall
    aDatabase -->|"SQL transactions"| aSqlite
```

### Technology Stack Summary

| Layer | Technology | Version | Purpose |
|---|---|---|---|
| Browser UI | React | `^19.0.0` | Dashboard, appointment management, and call screen |
| Frontend build | Vite | `^6.0.0` | Development server and production bundle |
| Browser audio | WebRTC and Web Audio API | Browser APIs | Microphone, speaker, peer connection, and local waveform |
| Backend runtime | Python | `>=3.11` | API and voice-pipeline runtime |
| HTTP and WebSocket API | FastAPI | `>=0.115` | Dashboard data, appointment routes, WebRTC signaling, and transcript socket |
| Voice orchestration | Pipecat | `>=1.17,<2` | Pipeline workers, service adapters, tool calling, and VAD integration |
| Speech recognition and synthesis | ElevenLabs | Provider API | Realtime STT and TTS |
| Language model | Groq `openai/gpt-oss-20b` | Configured model ID | Conversation and appointment-tool selection |
| Voice activity detection | Silero VAD | Pipecat integration | User-turn detection with a configured 0.4 second stop interval |
| Persistence | SQLite | Python standard library | Doctors, slots, appointments, and cancellation history |
| ASGI server | Uvicorn | `>=0.32` | Runs the FastAPI application |

### Data Storage and External Services

The `Database` adapter stores clinic data in SQLite tables for doctors, slots, and appointments. FastAPI routes and voice tools share the same adapter; booking and cancellation use database transactions, and a partial unique index prevents more than one active booking per slot. There is no cache or message broker. Transcript and appointment-update events are broadcast over the active WebSocket session and are not persisted as transcript history. Groq provides the LLM, ElevenLabs provides STT and TTS, and Silero VAD runs in the Pipecat pipeline.

### Key Architectural Decisions

- The dashboard and voice tools use one SQLite-backed `Database` abstraction, keeping schedule updates consistent across REST and voice interactions.
- `VoiceSessionManager` owns asynchronous pipeline tasks by WebRTC peer ID and cancels outstanding tasks during application shutdown.
- Transcript clients are isolated by session, origin-checked, and sent updates concurrently with a one-second timeout; application logs rotate at 5 MB with three backups.

## Component Relationships

<!-- mermaid-checked: no \n, no em-dash/en-dash, no {} in labels, subgraphs are id["label"], arrows are -->|"label"|, all subgraphs closed by end, ids unique -->
```mermaid
flowchart LR
    subgraph componentPresentation["Presentation"]
        cReactApp["React App component"]
        cApiClient["api.js HTTP client"]
    end
    subgraph componentApi["API Layer"]
        cFastApi["FastAPI app and routes"]
        cRequestModels["Pydantic request models"]
    end
    subgraph componentBusiness["Voice and Business Logic"]
        cSessionManager["VoiceSessionManager"]
        cRunBot["run_bot and appointment tools"]
        cObserver["TranscriptObserver"]
    end
    subgraph componentData["Data Access"]
        cDatabase["Database"]
        cSqlite["SQLite tables"]
    end
    subgraph componentInfrastructure["Session Infrastructure"]
        cTranscriptManager["TranscriptManager"]
        cOriginGuard["CORS and WebSocket origin guard"]
        cLogging["Rotating application logging"]
    end

    cReactApp -->|"JSON API calls"| cApiClient
    cApiClient -->|"HTTP requests"| cFastApi
    cReactApp -->|"WebRTC signaling and transcript socket"| cFastApi
    cFastApi -->|"validates request bodies"| cRequestModels
    cFastApi -->|"doctor and appointment operations"| cDatabase
    cFastApi -->|"starts call task"| cSessionManager
    cSessionManager -->|"creates pipeline task"| cRunBot
    cRunBot -->|"voice appointment tools"| cDatabase
    cRunBot -->|"adds caller and assistant observers"| cObserver
    cObserver -->|"publishes transcript frames"| cTranscriptManager
    cRunBot -->|"publishes appointment events"| cTranscriptManager
    cTranscriptManager -->|"WebSocket event messages"| cReactApp
    cDatabase -->|"SQL reads and writes"| cSqlite
    cOriginGuard -.->|"protects API and transcript origins"| cFastApi
    cLogging -.->|"records lifecycle and failures"| cFastApi
    cLogging -.->|"records task outcomes"| cSessionManager
    cLogging -.->|"records pipeline and tool activity"| cRunBot
```

### Component Inventory

| Component | Layer | Type | Responsibility |
|---|---|---|---|
| `App` | Presentation | React component | Dashboard state, call lifecycle, WebRTC, transcript handling, and appointment feedback |
| `api.js` | Presentation | HTTP client module | Sends JSON requests to the configured backend origin and surfaces API errors |
| FastAPI app | API | Application factory and route handlers | Validates requests, serves dashboard data, handles WebRTC signaling, and accepts transcript sockets |
| Pydantic request models | API | Validation models | Constrain doctor, slot, booking, cancellation, offer, and ICE-candidate input |
| `VoiceSessionManager` | Business logic | Async task manager | Starts one voice task per peer ID and manages task shutdown |
| `run_bot` | Voice runtime | Pipeline factory and runner | Configures Pipecat, registers tools, queues the greeting, and runs the call worker |
| `TranscriptObserver` | Voice runtime | Pipecat frame processor | Publishes caller transcription frames and assistant text frames |
| `Database` | Data access | SQLite adapter | Initializes schema and performs doctor, slot, booking, and cancellation operations |
| `TranscriptManager` | Infrastructure | WebSocket broadcaster | Sends session-scoped transcript and appointment events and removes slow clients |
| Origin guard and logging | Infrastructure | Middleware and cross-cutting services | Restricts browser origins and records application, session, and pipeline lifecycle events |

## Class Diagram

This class and domain relationship view uses the supported flowchart notation. Solid arrows show ownership or use; the labels describe the relationship.

<!-- mermaid-checked: no \n, no em-dash/en-dash, no {} in labels, subgraphs are id["label"], arrows are -->|"label"|, all subgraphs closed by end, ids unique -->
```mermaid
flowchart LR
    subgraph classClientLayer["Client"]
        kApp["App"]
        kApiClient["api.js"]
    end
    subgraph classServiceLayer["Application Services"]
        kAppFactory["create_app and FastAPI routes"]
        kInputModels["Pydantic input models"]
        kSessionManager["VoiceSessionManager"]
        kRunBot["run_bot and create_tools"]
        kObserver["TranscriptObserver"]
        kTranscriptManager["TranscriptManager"]
        kDatabase["Database"]
    end
    subgraph classDataLayer["SQLite Domain Records"]
        kDoctor["doctors table"]
        kSlot["slots table"]
        kAppointment["appointments table"]
    end

    kApp -->|"uses"| kApiClient
    kApiClient -->|"sends REST requests"| kAppFactory
    kApp -->|"opens WebRTC and transcript sessions"| kAppFactory
    kAppFactory -->|"validates with"| kInputModels
    kAppFactory -->|"uses"| kDatabase
    kAppFactory -->|"starts"| kSessionManager
    kSessionManager -->|"runs"| kRunBot
    kRunBot -->|"uses"| kDatabase
    kRunBot -->|"installs in pipeline"| kObserver
    kObserver -->|"publishes through"| kTranscriptManager
    kRunBot -->|"publishes tool events through"| kTranscriptManager
    kDatabase -->|"reads and writes"| kDoctor
    kDatabase -->|"reads and writes"| kSlot
    kDatabase -->|"reads and writes"| kAppointment
    kDoctor -->|"has many"| kSlot
    kSlot -->|"has booking history"| kAppointment
```

### Class and Domain Inventory

| Class or record | Kind | Key relationship |
|---|---|---|
| `App` | React function component | Uses `api.js`; owns client-side call and dashboard state |
| `api.js` | JavaScript module | Sends REST requests to FastAPI; the component also opens WebRTC and WebSocket connections |
| `create_app` | Application factory | Creates the FastAPI app and shared `Database`, `TranscriptManager`, and `VoiceSessionManager` instances |
| Pydantic input models | Request data models | Validate API payloads before route handlers call the database or WebRTC handler |
| `VoiceSessionManager` | Python class | Starts and tracks `run_bot` tasks by peer connection ID |
| `run_bot` and `create_tools` | Python functions | Assemble the Pipecat pipeline and expose availability, booking, and cancellation tools |
| `TranscriptObserver` | Pipecat processor subclass | Publishes caller and assistant text through `TranscriptManager` |
| `TranscriptManager` | Python class | Manages WebSocket clients and broadcasts transcript and appointment events |
| `Database` | Python class | Owns SQLite connection and transaction operations for the domain tables |
| `doctors` | SQLite table | Parent record for doctor availability slots |
| `slots` | SQLite table | Belongs to a doctor and may have appointment history |
| `appointments` | SQLite table | References a slot and stores its booking or cancellation status |

`TranscriptProcessor` is also defined in `transcripts.py`, but the active `run_bot` pipeline currently inserts `TranscriptObserver` instances for caller and assistant frames.

## Call Flow Diagram

<!-- mermaid-checked: no \n, no em-dash/en-dash, no {} in labels, subgraphs are id["label"], arrows are -->|"label"|, all subgraphs closed by end, ids unique -->
```mermaid
flowchart TD
    fStart(("Caller starts call"))
    fMic["Request microphone and create WebRTC offer"]
    fApi["FastAPI validates voice configuration and offer"]
    fSession["VoiceSessionManager starts task by peer ID"]
    fPipeline["Build and run Pipecat pipeline"]
    fGreeting["Queue opening greeting after pipeline starts"]
    fGreetingTts["ElevenLabs synthesizes greeting"]
    fGreetingAudio["WebRTC plays greeting to caller"]
    fCallerAudio["Caller speaks through WebRTC"]
    fStt["ElevenLabs realtime speech recognition"]
    fCallerTranscript["Publish caller transcript"]
    fVad["Silero VAD detects user turn"]
    fLlm["Groq LLM interprets request"]
    fToolChoice["Choose availability, booking, or cancellation"]
    fAvailability["Check doctor and available slots"]
    fPhoneReadback["Read phone number back to caller"]
    fPhoneConfirmed["Phone confirmation received?"]
    fAskConfirm["Ask for confirmation without changing data"]
    fBook["Book selected slot"]
    fCancelLookup["Find matching active appointments"]
    fAskWhich["Ask caller to choose when several match"]
    fCancel["Cancel selected appointment"]
    fAvailabilityDb["Database and SQLite slot lookup"]
    fBookDb["Database booking transaction"]
    fCancelCheck["More than one active match?"]
    fCancelDb["Database cancellation transaction"]
    fToolResult["Return tool result to Groq"]
    fAssistantTranscript["Publish assistant text transcript"]
    fResponseTts["ElevenLabs synthesizes response"]
    fResponseAudio["WebRTC plays response"]
    fAppointmentEvent["Publish booking or cancellation event"]
    fTranscriptSocket["Transcript WebSocket"]
    fDashboardUpdate["Show confirmation, refresh schedule, highlight row"]
    fEnd(("Caller ends call"))

    fStart -->|"request access"| fMic
    fMic -->|"POST offer"| fApi
    fApi -->|"start peer session"| fSession
    fSession -->|"run"| fPipeline
    fPipeline -->|"on pipeline started"| fGreeting
    fGreeting -->|"speech frame"| fGreetingTts
    fGreetingTts -->|"opening audio"| fGreetingAudio
    fGreetingAudio -->|"greeting complete"| fCallerAudio
    fCallerAudio -->|"incoming audio"| fStt
    fStt -->|"transcription frame"| fCallerTranscript
    fCallerTranscript -->|"text"| fTranscriptSocket
    fCallerTranscript -->|"forwards caller frames"| fVad
    fVad -->|"completed turn"| fLlm
    fLlm -->|"direct response"| fAssistantTranscript
    fLlm -->|"select tool"| fToolChoice
    fToolChoice -->|"availability request"| fAvailability
    fAvailability -->|"read schedule"| fAvailabilityDb
    fAvailabilityDb -->|"available slots"| fToolResult
    fToolChoice -->|"booking request"| fPhoneReadback
    fToolChoice -->|"cancellation request"| fPhoneReadback
    fPhoneReadback -->|"ask for explicit yes"| fPhoneConfirmed
    fPhoneConfirmed -->|"no"| fAskConfirm
    fAskConfirm -->|"continue conversation"| fLlm
    fPhoneConfirmed -->|"yes for booking"| fBook
    fBook -->|"write booking"| fBookDb
    fBookDb -->|"tool result"| fToolResult
    fBookDb -->|"booking committed"| fAppointmentEvent
    fPhoneConfirmed -->|"yes for cancellation"| fCancelLookup
    fCancelLookup -->|"query active appointments"| fCancelCheck
    fCancelCheck -->|"multiple matches"| fAskWhich
    fAskWhich -->|"caller selects doctor and time"| fLlm
    fCancelCheck -->|"one match or selected ID"| fCancel
    fCancel -->|"write cancellation"| fCancelDb
    fCancelDb -->|"tool result"| fToolResult
    fCancelDb -->|"cancellation committed"| fAppointmentEvent
    fToolResult -->|"continue response"| fLlm
    fLlm -->|"assistant text"| fAssistantTranscript
    fAssistantTranscript -->|"text event"| fTranscriptSocket
    fLlm -->|"response text"| fResponseTts
    fResponseTts -->|"speech audio"| fResponseAudio
    fResponseAudio -->|"next caller turn"| fCallerAudio
    fAppointmentEvent -->|"structured event"| fTranscriptSocket
    fTranscriptSocket -->|"live UI updates"| fDashboardUpdate
    fDashboardUpdate -->|"caller continues"| fCallerAudio
    fCallerAudio -->|"hang up"| fEnd
```

## Pipeline Description

1. **Call setup and signaling:** The React call screen requests microphone access, creates an `RTCPeerConnection`, and sends its offer to `POST /api/offer`. FastAPI checks that the Groq and ElevenLabs settings exist, then passes the offer to Pipecat's `SmallWebRTCRequestHandler`. The browser opens a session-scoped transcript WebSocket after receiving the WebRTC answer.
2. **Session lifecycle:** The connection callback asks `VoiceSessionManager` to start `run_bot` using the peer connection ID as the session ID. The manager tracks the asynchronous worker and cancels active workers when the API shuts down.
3. **Opening greeting:** When the Pipecat pipeline starts, its worker queues a `TTSSpeakFrame`. ElevenLabs synthesizes the greeting and the WebRTC transport sends it to the browser before the caller's first turn.
4. **Caller input and turn detection:** Incoming WebRTC audio is transcribed by `ElevenLabsRealtimeSTTService`. A caller `TranscriptObserver` publishes each recognized utterance to `TranscriptManager`. The LLM user aggregator uses `SileroVADAnalyzer` with a 0.4 second stop interval to identify the end of the caller's turn.
5. **Reasoning and tools:** `GroqLLMService` uses the `openai/gpt-oss-20b` model and the receptionist system prompt. It can check doctor availability, book a selected slot, or cancel an appointment. Booking and cancellation require the caller's spoken phone number to be read back and explicitly confirmed; if several appointments match a cancellation, the assistant asks the caller to choose one. Tool database operations run through `asyncio.to_thread` so SQLite work does not block the voice event loop.
6. **Assistant response:** Assistant text is published by a second `TranscriptObserver`, then passed to `ElevenLabsTTSService`. The resulting audio is sent through the WebRTC transport to the browser speaker. Transcript messages are streamed to the active call screen rather than stored as history.
7. **Schedule updates:** After a successful booking or cancellation, the tool publishes a structured appointment event through `TranscriptManager`. The browser displays the confirmation, refreshes the dashboard, and highlights the matching row. The same SQLite database backs both the voice tools and REST dashboard.
8. **Call teardown:** The browser closes its transcript socket, peer connection, and media tracks when the caller ends the call. Pipecat task completion is observed by `VoiceSessionManager`; application shutdown also closes the WebRTC request handler and cancels any remaining pipeline tasks.
