# Chalchitra

**Chalchitra** is a self-hosted, open-source AI video studio. It turns a deck-walkthrough
video into a narrated, lip-synced avatar video — transcription, scripting, voice cloning,
and talking-head animation, all running on your own infrastructure. No token costs, no data
leaves your network.

> Chalchitra (चलचित्र) means "moving picture."

---

## What it does

- **Video studio** — upload a deck walkthrough → get avatar-narrated clips, one per section.
- **Voice cloning** — clone any voice from a short sample (Chatterbox Multilingual, 23 languages).
- **Lip-sync avatars** — SadTalker renders a talking head aligned to the spoken audio.
- **API pipelines** — Virality Clips (auto highlight cutter) and Podcast Studio (chapters, captions, final stitch).
- **Auth & multi-user** — JWT auth, per-user data isolation, everything owned per account.

The full pipeline has been verified end to end through the UI, producing a real lip-synced clip.

---

## Architecture (high level)

```
Frontend (Next.js)  ──►  Backend API (FastAPI)  ──►  Celery workers
                                │                         │
                                ▼                         ├─ Whisper (transcription)
                          Postgres / Redis                ├─ Ollama / LLM (section scripts)
                                                          ├─ Chatterbox (voice / TTS)
                                                          └─ SadTalker service (lip-sync)
```

Everything runs in Docker Compose. Voice profiles and models live on shared volumes so all
workers can read them.

---

## Prerequisites

- Git
- Docker + Docker Compose
- For real-time lip-sync: an NVIDIA GPU with drivers + the NVIDIA Container Toolkit
  (CPU works too, but SadTalker rendering is very slow — GPU is strongly recommended)

---

## Setup & run

### 1. Clone

```
git clone https://github.com/FDC-innovation/fdcai-talk.git
cd fdcai-talk
git checkout feature/final-demo
```

### 2. Environment

Place the shared `.env` in the project root (next to `docker-compose.yml`). For a GPU box,
confirm these values:

| Setting | Value |
|---|---|
| `TTS_FORCE_EDGE` | remove it or set `0` (enables real voice cloning) |
| `AVATAR_ENGINE` | `sadtalker` |
| `SADTALKER_URL` | `http://sadtalker:5003` |

### 3. Start the stack

```
docker compose up -d
docker compose ps
```

First run downloads models (Whisper, LLM). Make sure **ollama** is running — the
scripting step needs it.

### 4. Start the SadTalker service

```
docker compose --profile cpu-avatar up -d sadtalker
curl -sf http://localhost:5003/health     # expect {"status":"ok"}
```

### 5. Pull the language model (first time only)

```
docker compose exec ollama ollama pull llama3.2:1b
```

### 6. Open the UI

```
http://localhost:3000
```

Test login: `exp2@example.com` / `TestPass123` (or register a new account).

### 7. Make a video

1. **Avatars** tab → upload a clear front-facing photo → name it.
2. **Video** tab → upload a short deck video → **click the presenter to select it**.
3. *(Optional)* toggle **Clone a voice** → upload a 10–60s clip or pick a saved voice.
4. Choose a language → **Generate**.
5. Watch the stages → preview & download each clip.

On GPU the clips come back lip-synced and render fast. On CPU the same pipeline runs but
SadTalker rendering is slow (minutes per clip).

---

## Ports

| Service | URL |
|---|---|
| Web UI | `http://localhost:3000` |
| Backend API | `http://localhost:8000` (`/api/v1`) |
| SadTalker | `http://localhost:5003/health` |

---

## Troubleshooting

- **Job fails at ~10% with "Connection error"** → ollama isn't running. `docker compose start ollama`, then retry.
- **Clips have no lip-sync** → check `AVATAR_ENGINE=sadtalker` and `SADTALKER_URL=http://sadtalker:5003`, then `docker compose up -d backend celery-worker-gpu`.
- **Voice cloning not applied** → make sure `TTS_FORCE_EDGE` is off, then `docker compose up -d celery-worker-gpu`.
- **`flower` container restarting** → harmless, ignore it.
- After any `.env` change: `docker compose up -d backend celery-worker-gpu`.

---

## Notes

- The explainer avatar step calls the SadTalker HTTP service (`AVATAR_ENGINE=sadtalker`).
  Without a configured engine it falls back to a simple non-lip-sync clip.
- SadTalker rendering is GPU-bound; on CPU it works but is slow (this is expected).
- `video_raw.logs` in the repo is a captured run log showing the full pipeline executing
  end to end (transcribe → sections → voice → SadTalker render → clip).
