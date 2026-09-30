# 🎙️ Fireflies Notetaker

A **Fireflies.ai-style meeting notetaker** built from scratch — focused on Google Meet for Phase 1.

> **One-line pitch:** After a Google Meet ends, automatically fetch the transcript, generate an AI summary with key decisions and action items, and display it in a clean dashboard.

---

## Architecture

```
Google Meet (conferenceRecords REST API)
         │
         ▼
providers/google_meet.py         ← Only place that knows about Google Meet
(fetches transcript via Meet API)
         │  NormalizedTranscript
         ▼
services/processor.py            ← Orchestrates the pipeline
         │
    ┌────┴─────────────┐
    ▼                  ▼
database/repository.py      ai/summarizer.py
(SQLAlchemy + SQLite)       (OpenAI-compatible LLM)
    │                  │
    └─────────┬────────┘
              ▼
         api/routes.py           ← FastAPI REST API
              │
              ▼
         frontend/               ← Vanilla HTML/CSS/JS
```

### Processing State Machine

```
SCHEDULED → CAPTURING → PROCESSING → TRANSCRIBED → SUMMARIZING → COMPLETED
                                                                      ↕
                                                                   FAILED
```

### Provider Abstraction

All platform-specific code lives behind `AbstractMeetingProvider`. Adding Zoom or Teams:
1. Create `providers/zoom.py` subclassing `AbstractMeetingProvider`
2. Implement `fetch_transcript()` returning `NormalizedTranscript`
3. Register in `providers/__init__.py`

The processor, summarizer, and API never need to change.

---

## Folder Structure

```
fireflies-notetaker/
├── backend/
│   ├── main.py                  # FastAPI app + startup
│   ├── config.py                # Environment variables (pydantic-settings)
│   ├── api/
│   │   └── routes.py            # All REST endpoints
│   ├── models/
│   │   ├── meeting.py           # MeetingORM + Pydantic schemas + MeetingStatus
│   │   └── transcript.py        # NormalizedTranscript (platform-agnostic format)
│   ├── providers/
│   │   ├── base.py              # AbstractMeetingProvider protocol
│   │   └── google_meet.py       # Google Meet impl + MockGoogleMeetProvider
│   ├── ai/
│   │   └── summarizer.py        # LLM summarization (OpenAI-compatible)
│   ├── services/
│   │   └── processor.py         # End-to-end pipeline orchestrator (idempotent)
│   ├── database/
│   │   └── repository.py        # Async SQLAlchemy CRUD (SQLite / PostgreSQL)
│   └── workers/
│       └── background.py        # Fire-and-forget asyncio task runner
│
├── frontend/
│   ├── index.html               # Meetings list page
│   ├── meeting.html             # Meeting detail page
│   ├── css/style.css            # Premium dark glassmorphism design
│   └── js/
│       ├── api.js               # API client module
│       ├── meetings.js          # Meetings list logic
│       └── meeting-detail.js    # Detail page + polling
│
├── tests/
│   ├── conftest.py              # Shared fixtures (in-memory DB, mocks)
│   ├── test_normalize.py        # Transcript normalization tests
│   ├── test_summarizer.py       # AI summarizer tests
│   ├── test_processor.py        # Pipeline + idempotency tests
│   ├── test_api.py              # API endpoint tests
│   └── fixtures/
│       └── sample_google_meet_transcript.json   # Mock transcript for testing
│
├── .env.example
├── requirements.txt
├── pytest.ini
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the values:

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | **Yes** | OpenAI API key (or OpenRouter key if using `OPENAI_BASE_URL`) |
| `LLM_MODEL` | No | LLM model name (default: `gpt-4o-mini`) |
| `OPENAI_BASE_URL` | No | Override for OpenRouter, Groq, Together, etc. |
| `GOOGLE_CLIENT_ID` | For real Meet | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | For real Meet | Google OAuth client secret |
| `GOOGLE_REFRESH_TOKEN` | For real Meet | Google OAuth refresh token (offline access) |
| `DATABASE_URL` | No | SQLite (default) or PostgreSQL URL |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |

### Using OpenRouter instead of OpenAI

```env
OPENAI_API_KEY=sk-or-v1-your-openrouter-key
OPENAI_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=openai/gpt-4o-mini
```

---

## How to Install

```bash
# 1. Clone / navigate to the project
cd fireflies-notetaker

# 2. Create a virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate    # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your .env file
copy .env.example .env        # Windows
# cp .env.example .env        # macOS/Linux

# 5. Edit .env with your OPENAI_API_KEY (minimum required)
```

---

## How to Run the Backend

```bash
cd backend
uvicorn main:app --reload --port 8000
```

The server starts at **http://localhost:8000**.

- **Frontend:** http://localhost:8000/
- **API docs:** http://localhost:8000/docs (Swagger UI)
- **Health:** http://localhost:8000/api/health

---

## How to Run with Docker

```bash
# Build and start
docker-compose up --build

# Stop
docker-compose down
```

---

## How Google Meet Integration Works

The Google Meet provider (`providers/google_meet.py`) uses the **Google Meet REST API**:

### API used
- `GET /v2/conferenceRecords` — list conference records for a meeting
- `GET /v2/conferenceRecords/{name}/transcripts` — list transcripts
- `GET /v2/conferenceRecords/{name}/transcripts/{id}/entries` — fetch utterances (speaker + text + timestamps)

### Requirements for real transcripts
1. Meeting host must **enable transcription** (Recording → Transcription) in Google Meet settings
2. You need OAuth 2.0 credentials with scope: `https://www.googleapis.com/auth/meetings.space.readonly`
3. The meeting must have ended (transcripts are only available post-meeting)

### Getting Google OAuth credentials
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable **Google Meet API**
3. Create OAuth 2.0 Client ID (Desktop app type)
4. Run the OAuth flow to get a refresh token with offline access
5. Add to `.env`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`

---

## How Transcript Processing Works

```
1. POST /api/meetings/{id}/process
         │
         ▼
2. CAPTURING: providers/google_meet.py
   → fetch conferenceRecords
   → fetch transcriptEntries
   → parse speaker names + timestamps
         │
         ▼
3. PROCESSING: normalize into NormalizedTranscript
   {meeting_id, platform, segments: [{speaker, start, end, text}]}
         │
         ▼
4. TRANSCRIBED: store transcript in SQLite
         │
         ▼
5. SUMMARIZING: ai/summarizer.py
   → send transcript to LLM with structured prompt
   → parse JSON response
         │
         ▼
6. COMPLETED: store summary in SQLite
   {overview, key_points, decisions, action_items, participants}
```

---

## How AI Summarization Works

The summarizer (`ai/summarizer.py`) sends the normalized transcript to an OpenAI-compatible LLM with a structured JSON prompt requesting:

```json
{
  "title": "Short descriptive meeting title",
  "overview": "2-4 sentence executive summary",
  "key_points": ["..."],
  "decisions": ["..."],
  "action_items": [
    {"task": "...", "owner": "...", "due_date": "..."}
  ],
  "participants": ["Name 1", "Name 2"]
}
```

**Failure handling:**
- On LLM error → returns a fallback summary (meeting still marked COMPLETED)
- On JSON parse error → returns a fallback summary
- Never crashes the pipeline

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `POST` | `/api/meetings` | Create a meeting record |
| `GET` | `/api/meetings` | List all meetings |
| `GET` | `/api/meetings/{id}` | Get meeting details + summary |
| `DELETE` | `/api/meetings/{id}` | Delete a meeting |
| `POST` | `/api/meetings/{id}/process` | Fetch transcript + generate AI summary |
| `GET` | `/api/meetings/{id}/summary` | Get just the summary |
| `GET` | `/api/meetings/{id}/transcript` | Get just the transcript |
| `POST` | `/api/meetings/mock` | Create + process a mock meeting (no Google creds needed) |

### Query params for `/process`
- `?mock=true` — use mock transcript (no Google credentials required)
- `?force=true` — reprocess even if already COMPLETED

---

## How to Test with Mock Data

**Option 1: Quick Demo button (fastest)**

1. Start the server
2. Open http://localhost:8000
3. Click **⚡ Quick Demo** — creates a meeting with a realistic pre-built transcript and runs it through the full AI pipeline
4. You'll be redirected to the meeting detail page showing the generated summary

**Option 2: API call**

```bash
# Create a mock meeting and process it immediately
curl -X POST http://localhost:8000/api/meetings/mock

# Returns a COMPLETED meeting with full summary
```

**Option 3: Run the test suite**

```bash
cd fireflies-notetaker
python -m pytest tests/ -v
```

---

## How to Test with a Real Google Meet

1. Set up Google credentials (see above)
2. Host a Google Meet with **transcription enabled**
3. After the meeting ends, get the conference record ID from the Meet API or URL
4. Create a meeting record:

```bash
curl -X POST http://localhost:8000/api/meetings \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "google_meet",
    "title": "My Meeting",
    "meeting_url": "https://meet.google.com/abc-def-ghi",
    "external_meeting_id": "conferenceRecords/your-record-id"
  }'
```

5. Note the `id` from the response
6. Trigger processing:

```bash
curl -X POST http://localhost:8000/api/meetings/{id}/process
```

7. Poll until `status == "completed"`:

```bash
curl http://localhost:8000/api/meetings/{id}
```

8. View the summary:

```bash
curl http://localhost:8000/api/meetings/{id}/summary
```

---

## What Is Implemented

✅ Google Meet transcript provider (real API + mock)  
✅ Provider abstraction (ready for Zoom/Teams)  
✅ Normalized transcript format (platform-agnostic)  
✅ AI summarization with structured output  
✅ Idempotent processing pipeline  
✅ Meeting status state machine (6 states + FAILED)  
✅ SQLite database (ready to switch to PostgreSQL)  
✅ Full REST API  
✅ Premium dark-mode frontend (meetings list + detail)  
✅ Status polling in frontend  
✅ Mock meeting for end-to-end testing  
✅ Comprehensive test suite  
✅ Error handling + graceful fallbacks  
✅ Background processing (non-blocking)  

---

## What Is Intentionally Postponed

The following are out of scope for Phase 1 but the architecture is ready for them:

| Feature | Rationale |
|---|---|
| **Zoom provider** | Add `providers/zoom.py` implementing `AbstractMeetingProvider` |
| **Teams provider** | Add `providers/teams.py` |
| **Real-time capture** (WebRTC/bot) | Requires media server, much more complex |
| **Browser bot** (Playwright/nodriver) | Joins meeting as participant; involves Kubernetes fleet |
| **Recall.ai / Meeting BaaS** | 3rd-party hosted bots; add as provider |
| **Kafka event pipeline** | Replace `workers/background.py` with Kafka consumer |
| **WebSocket live transcription** | Add WebSocket route + streaming LLM |
| **Speaker diarization** | STT with per-speaker audio tracks |
| **AssemblyAI / Deepgram STT** | Add as alternative to Meet API transcripts |
| **Google Calendar webhook** | Auto-discover meetings from calendar push events |
| **Transcript chunking** | For meetings > 2 hours, chunk before LLM |
| **Redis for dedup** | Replace in-memory task set with Redis for multi-process |
| **PostgreSQL** | Already supported via `DATABASE_URL` env var |
| **Multi-tenancy** | Add `tenant_id` column to meetings table |
| **Auth/JWT** | Add OAuth2 login to protect the frontend |
| **Email notifications** | Send summary email after meeting |
| **Slack integration** | Post summary to Slack channel |

---

## Next Steps for Real-Time Audio Capture

The current architecture handles **post-meeting transcript fetching** via the Google Meet REST API. To add live capture (Fireflies-style):

```
Phase 2A: Hosted Bot API
─────────────────────────
Use Recall.ai or Meeting BaaS to join meetings as a bot.
These services handle WebRTC/audio extraction and return
transcripts via webhook. Add RecallProvider in providers/recall.py.
No infrastructure changes needed.

Phase 2B: Own Browser Bot
──────────────────────────
Playwright-based bot joins Google Meet as a participant.
Taps WebRTC audio stream via injected JavaScript.
Streams audio to Deepgram STT via WebSocket.
One Kubernetes Job per meeting.
See: notetaker_service/live_meeting_bot/ in existing project for reference.

Phase 2C: LiveKit / Media Server
──────────────────────────────────
RTMP ingest from bot → Kafka → STT worker → transcript segments.
Real-time display via WebSocket.
Requires significant infrastructure (K8s, Kafka, LiveKit cluster).
```

---

## Architecture Notes

### Why SQLite (not MongoDB)?
The existing project uses MongoDB but it's tightly coupled to the BlazeUp monorepo's connection pooling and Atlas configuration. SQLite requires zero external setup, making this project runnable in seconds. PostgreSQL is already supported via `DATABASE_URL` env var.

### Why OpenAI SDK (not LangChain)?
LangChain adds complexity for a use case this straightforward. The OpenAI SDK supports any OpenAI-compatible endpoint (OpenRouter, Together, Groq, Ollama) via `base_url` override.

### Idempotency guarantee
A meeting is never double-processed. `processor.py` checks the current status before doing anything:
- `COMPLETED` → skip (unless `force=True`)
- `CAPTURING/PROCESSING/SUMMARIZING` → skip (already running)
- `FAILED` / `SCHEDULED` → proceed
