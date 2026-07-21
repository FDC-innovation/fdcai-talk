# FDCAI Talk — AI Avatar & Video Pipeline System

## 1. Project Overview

FDCAI Talk is a real-time AI avatar conversation system combined with a set of asynchronous video-processing pipelines. Users upload a photo to create an "avatar," clone their voice, and hold a live spoken conversation with an LLM-driven character that talks back with lip-synced video (MuseTalk) — while separately, longer-form video jobs (clip extraction, podcast chapterization, SadTalker portrait animation) run in the background via Celery.

It is built for teams building conversational-AI avatar products or automated video-editing tooling: a FastAPI backend, a Next.js frontend, and GPU-hungry ML services (Whisper, Chatterbox TTS, MuseTalk, SadTalker, Ollama/Claude/GPT) stitched together with Celery + Redis + Postgres.

## 2. Architecture

**Core stack:** FastAPI (async, WebSocket) backend, Next.js 16 frontend, PostgreSQL, Redis (cache + Celery broker/backend), Celery workers split into `cpu` and `gpu` queues, Ollama (local LLM option), and a standalone SadTalker GPU microservice.

```
                    ┌─────────────┐
                    │   Browser   │
                    └──────┬──────┘
                            │  HTTPS / WSS
                    ┌──────▼──────┐
                    │    Nginx    │  (optional reverse proxy, docker-compose profile)
                    └──────┬──────┘
                            │
        ┌───────────────────┼────────────────────┐
        │                    │                     │
┌───────▼───────┐   ┌────────▼────────┐  ┌────────▼────────┐
│  Next.js       │   │  FastAPI backend │  │  Static /uploads │
│  frontend:3000 │   │  backend:8000    │  │  (local storage)  │
└────────────────┘   └───┬────┬────┬───┘  └───────────────────┘
                          │    │    │
     REST + WS auth ──────┘    │    └── /health, /metrics (Prometheus)
                                │
         ┌──────────────────────┼───────────────────────┐
         │                      │                        │
┌────────▼────────┐   ┌─────────▼─────────┐   ┌──────────▼─────────┐
│  Postgres        │   │  Redis             │   │  Ollama (optional)  │
│  users/avatars/   │   │  cache, rate-limit, │   │  local LLM_PROVIDER │
│  sessions/jobs     │   │  Celery broker+     │   │  =ollama            │
└────────────────┘   │  result backend     │   └─────────────────────┘
                       └─────────┬───────────┘
                                  │  celery send_task(queue=cpu|gpu)
                  ┌────────────────┴─────────────────┐
                  │                                    │
        ┌──────────▼──────────┐              ┌─────────▼──────────┐
        │ celery-worker (cpu)  │              │ celery-worker-gpu   │
        │ clips, podcast jobs   │              │ sadtalker jobs       │
        └──────────┬──────────┘              └─────────┬──────────┘
                    │                                    │
        ffmpeg / faster-whisper / LLM           HTTP call to SadTalker
        (in-process, same container)            service (services/sadtalker,
                                                  profile: gpu, port 5003)

  Real-time voice/video chat (WebSocket /ws/session/{id}) bypasses Celery
  entirely — it runs in-process inside the FastAPI backend:
  mic audio → faster-whisper STT → Claude/GPT/Ollama (streamed) →
  Chatterbox/Edge/gTTS TTS → MuseTalk (persistent subprocess worker) →
  local/S3 storage → video_chunk events back over the socket.
```

Key architectural facts:
- The **Job pipelines** (`clips`, `podcast`, `sadtalker`) and the **real-time chat pipeline** (MuseTalk) are two completely separate code paths. `POST /api/v1/jobs/{pipeline}` only accepts `clips`, `sadtalker`, `podcast` (`VALID_PIPELINES` in `backend/app/api/v1/jobs.py:18`) — MuseTalk is *not* a Job pipeline; it's invoked directly by `app/websocket.py` via `app/services/animator.py` on every chat turn.
- Celery has two queues (`backend/app/celery_app.py`, routing in `backend/app/api/v1/jobs.py:59`): `cpu` (clips, podcast — plain CPU work: STT, LLM, ffmpeg) and `gpu` (sadtalker). `docker-compose.yml` runs one worker container per queue (`celery-worker`, `celery-worker-gpu`).
- All async jobs are tracked in a single `jobs` Postgres table (`backend/app/models.py:153`) with `status` (`pending → running → done/failed`) and `progress` (0-100), polled via `GET /api/v1/jobs/{id}`.

## 3. All Pipelines

### 3.1 Clips (`pipeline=clips`)
**What it does:** Finds and cuts short, "clip-worthy" highlight segments out of a longer video/audio file using LLM-driven transcript analysis.

**Input params** (`JobCreate.params`, arbitrary JSON):
- `media_path` (required) — server-local filesystem path to the source media
- `instructions` (optional) — free-text guidance, e.g. "find 30-60 second clips about pricing"; parsed for a duration range (`parse_duration_range` in `clip_detector.py`), defaults to 20-90s

**Workflow** (`run_pipeline_job` in `backend/app/celery_app.py:190`):
1. `stt_service.transcribe_with_words()` — faster-whisper transcription with word-level timestamps (progress 10→50)
2. `detect_clips()` (`app/services/clip_detector.py`) — groups words into sentences, chunks the transcript (~5500 chars/chunk), prompts the LLM per chunk with `[t=NNNs]` timestamp markers to find clip-worthy moments, validates duration bounds, snaps to sentence boundaries, and de-overlaps (progress →60)
3. `cut_clips()` (`app/services/clip_cutter.py`) — ffmpeg fast-seek + re-encode (`libx264`, CRF 18) per clip to `/tmp/videos/clips/` (progress →90)
4. Job marked `done`, `output = {clips, transcript, duration}`

**Output shape:**
```json
{
  "clips": [{"title": "...", "start_seconds": 12.3, "end_seconds": 54.1, "reason": "...", "file_path": "/tmp/videos/clips/<job>_clip0_....mp4", "cut_duration": 41.8, "status": "done"}],
  "transcript": "...",
  "duration": 812.4
}
```

**Status: WIRED BUT UNTESTED.** Code path is complete and exercised by no automated test (no `test_jobs*.py` / `test_clips*.py` in `backend/tests/`). `media_path` must already exist on the worker's filesystem — there is no upload endpoint or S3 fetch step, and the caller must supply a path the *Celery worker container* can read (shared `video_cache` volume only). Output `file_path` values are never exposed through any HTTP route — see §7.

### 3.2 Podcast (`pipeline=podcast`)
**What it does:** Splits a long recording into LLM-detected chapters, renders each chapter with a title-card intro and burned-in captions, and stitches everything into one final MP4.

**Input params:**
- `media_path` (required)
- `instructions` (optional) — e.g. "5 chapters", parsed by `parse_chapter_count` (`podcast_pipeline.py`); defaults to 2-5 chapters

**Workflow:**
1. `stt_service.transcribe_with_words()` (progress →40)
2. `detect_chapters()` — LLM call over up to 3000 words of transcript, returns contiguous chapters with title/subtitle/start/end; snapped so chapters tile the full duration with no gaps (progress →60)
3. `render_podcast()` (`app/services/podcast_pipeline.py`):
   - `cut_clips()` to extract each chapter's raw video
   - `make_chapter_card()` — ffmpeg-generated blurred/darkened background with the chapter title+subtitle drawn on it (3.5s)
   - `add_silent_audio()` on the card so concat doesn't break on mixed audio streams
   - `words_to_srt()` + `burn_srt()` — generates and hardcodes captions (5-word groups) onto the chapter video
   - `concat_files()` — card + captioned chapter → `chapter_NN.mp4`, then all chapters → `podcast_final.mp4` (progress →90)
4. `output = {chapters, final_video, transcript, duration}`

**Output shape:**
```json
{
  "chapters": [{"title": "...", "subtitle": "...", "start_seconds": 0, "end_seconds": 340, "rendered_path": "/tmp/videos/podcast/<job>/chapter_00.mp4", "status": "done"}],
  "final_video": "/tmp/videos/podcast/<job>/podcast_final.mp4",
  "transcript": "...",
  "duration": 1820.0
}
```

**Status: WIRED BUT UNTESTED.** Same caveats as Clips: no automated tests, requires `DejaVuSans-Bold.ttf` at a hardcoded path (`FONT_PATH` in `podcast_pipeline.py:17`) which is not confirmed installed in `backend/Dockerfile` (that font package is not among the apt packages installed — see §7), and output paths are never exposed over HTTP.

### 3.3 SadTalker (`pipeline=sadtalker`)
**What it does:** Generates a lip-synced talking-head video from a single still photo + an audio clip, by calling out to a separate SadTalker GPU microservice.

**Input params:**
- `image_url` (required) — URL reachable *from the SadTalker container*
- `audio_url` (required) — URL reachable *from the SadTalker container*

**Workflow** (`app/services/sadtalker_engine.py`):
1. If `SADTALKER_URL` is unset, the job returns immediately with `status: "not_configured"` — no crash
2. `POST {SADTALKER_URL}/generate` with `{image_url, audio_url}` (90-minute timeout — SadTalker's `inference.py` is slow, especially on CPU)
3. Response bytes written to `/tmp/videos/sadtalker/<job_id>/avatar.mp4`
4. `output = {status: "done", file_path, size_bytes}`

The SadTalker server itself (`services/sadtalker/server.py`) downloads the two URLs, shells out to `inference.py --still` (no `--enhancer`, deliberately, for speed), and returns the newest `.mp4` under `results/` as raw bytes.

**Output shape:**
```json
{"status": "done", "file_path": "/tmp/videos/sadtalker/<job_id>/avatar.mp4", "size_bytes": 2831044}
```

**Status: STUB / NOT PRODUCTION-CONFIGURED.**
- `docker-compose.yml`'s `sadtalker` service (`gpu` profile) builds `services/sadtalker/Dockerfile.gpu` — **that file does not exist in the repo** (only `services/sadtalker/Dockerfile`, which is explicitly CPU-only per its own comments: *"Self-hosted SadTalker, CPU-only... M1 constraint"*). `docker compose --profile gpu up` will fail to build as configured.
- Even when `SADTALKER_URL` is set in `.env`, neither `celery-worker` nor `celery-worker-gpu` in `docker-compose.yml` forward it (their `environment:` lists are hand-enumerated and omit `SADTALKER_URL`; only the `backend` service has `env_file: .env`). The worker that actually executes the sadtalker job will see `SADTALKER_URL` unset and always return `not_configured` inside Docker, even if it works fine when running the backend directly on the host.
- No automated test coverage.

### 3.4 MuseTalk (real-time avatar animation — not a Job pipeline)
**What it does:** Frame-accurate lip-sync animation engine used live inside the WebSocket chat pipeline (`AVATAR_ENGINE=musetalk` in `.env`) — turns "avatar photo + TTS audio for one sentence" into a short MP4, streamed to the browser as each sentence finishes.

**Input params:** N/A — not exposed as an HTTP job. Configured via `.env`: `AVATAR_ENGINE=musetalk`, `MUSETALK_PATH=models/MuseTalk`, `AVATAR_RESOLUTION`, `AVATAR_FPS`.

**Workflow** (`app/services/animator.py` + `backend/scripts/musetalk_worker.py`):
1. On first use, `AvatarAnimator.initialize()` locates the MuseTalk checkout at `MUSETALK_PATH` (relative to repo root or absolute); if missing, silently falls back to `AVATAR_ENGINE=simple` (static image + audio, no lip-sync) — logged as a warning, not an error
2. `_ensure_worker()` spawns `musetalk_worker.py` as a **persistent subprocess** communicating over stdin/stdout JSON lines, so the (large) VAE/UNet/Whisper/FaceParsing models are loaded exactly once and reused across every turn/session (60s GPU load / 5-10 min CPU load)
3. Per-avatar face-coordinate detection is cached to disk (`results/coords/<md5(avatar_path)>.pkl`) to skip landmark detection on repeat calls
4. Per sentence: worker extracts Whisper audio features → encodes the face crop to VAE latents → runs UNet diffusion per audio chunk → blends generated mouth region back into the original frame → streams PNG frames to disk → ffmpeg (`h264_nvenc` on GPU, `libx264` on CPU) muxes frames + audio into an MP4
5. If the worker dies (OOM, segfault) or any exception is raised, `animate()` catches it and falls back to `_animate_simple()` (ffmpeg static image + audio) so a chat turn never crashes on animation failure

**Output shape:** MP4 file at the caller-provided `output_path`; the WebSocket handler then uploads it via `storage_service` and emits `{"type": "video_chunk", "video_url": ..., "chunk_index": ..., "text": "<sentence>"}`.

**Status: PRODUCTION READY (with caveats).** This is the most exercised code path (it's the primary product feature), has real fallback behavior (worker crash → simple engine, models missing → simple engine), and is covered by `test_websocket.py`, `test_ws_e2e.py`, `test_ws_auth.py`. Caveats: requires a `models/MuseTalk` checkout + downloaded weights that are **not** part of this repo (`scripts/setup_musetalk.sh` fetches them separately) and meaningful throughput requires a real GPU — CPU inference is minutes per sentence, unusable for live chat.

## 4. API Reference

Base path: `/api/v1`. Auth: httpOnly cookie (`access_token`, set on `/users/login`) *or* `Authorization: Bearer <token>` header. When neither is present, most endpoints silently fall back to a synthetic `demo-user` (see §7).

### Users (`/api/v1/users`)
| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/register` | none | create account |
| POST | `/login` | none | OAuth2 form (`username`=email, `password`); sets cookie + returns bearer token |
| POST | `/logout` | none | clears cookie |
| GET | `/me` | required | current profile |
| PUT | `/me` | required | update profile/password |
| GET | `/` | required, superuser | list all users |
| GET | `/{user_id}` | required | own profile or superuser |

```bash
curl -X POST http://localhost:8000/api/v1/users/register \
  -H "Content-Type: application/json" \
  -d '{"email":"a@b.com","username":"alice","password":"secret123","full_name":"Alice"}'

curl -X POST http://localhost:8000/api/v1/users/login \
  -d "username=a@b.com&password=secret123" -c cookies.txt
# {"access_token":"eyJ...","token_type":"bearer"}
```

### Avatars (`/api/v1/avatars`)
| Method | Path | Notes |
|---|---|---|
| POST | `/upload` | multipart: `name` (form), `file` (image, ≤10MB) |
| GET | `/` | list current user's avatars (`skip`, `limit`) |
| GET | `/{avatar_id}` | fetch one |
| PUT | `/{avatar_id}/voice?voice_id=` | assign/unassign a cloned voice |
| PATCH | `/{avatar_id}/metadata` | merge `system_prompt`/`personality`/`background_color`/`animation_style` |
| PATCH | `/{avatar_id}/name` | rename |
| DELETE | `/{avatar_id}` | delete (cascades sessions) |

```bash
curl -X POST http://localhost:8000/api/v1/avatars/upload \
  -b cookies.txt -F "name=My Avatar" -F "file=@photo.jpg"
# {"id":"...","user_id":"...","image_url":"http://localhost:8000/uploads/avatars/.../image.jpg","status":"ready", ...}
```

### Sessions (`/api/v1/sessions`)
| Method | Path | Notes |
|---|---|---|
| POST | `/create` | body `{avatar_id, settings?}` |
| GET | `/` | list own sessions |
| GET | `/{session_id}` | fetch |
| POST | `/{session_id}/end` | mark ended, disconnects any live WS |
| GET | `/{session_id}/export` | download JSON transcript (capped at 5000 messages) |
| GET | `/{session_id}/download-video` | ffmpeg-concat all chunk videos into one MP4 download |
| DELETE | `/{session_id}` | delete |

```bash
curl -X POST http://localhost:8000/api/v1/sessions/create -b cookies.txt \
  -H "Content-Type: application/json" -d '{"avatar_id":"<uuid>"}'
```

### Conversations (`/api/v1/conversations`)
`GET /`, `GET /{id}`, `GET /session/{session_id}`, `POST /session/{session_id}`, `PATCH /{id}/rename`, `POST /{id}/summarize` (LLM-generated 2-3 sentence summary), `DELETE /{id}`.

### Messages (`/api/v1/messages`)
`POST /send` (REST fallback — prefer WebSocket), `GET /session/{session_id}`, `GET /{id}`, `PATCH /{id}`, `DELETE /{id}`.

### Voices (`/api/v1/voices`)
| Method | Path | Notes |
|---|---|---|
| POST | `/clone` | multipart `audio` (WAV/WebM/MP3, 10-60s, ≤20MB), `name`, `language` — creates a voice profile |
| GET | `/` | list own voices |
| GET | `/{voice_id}` | fetch metadata |
| POST | `/{voice_id}/synthesize` | form `text` (≤240 chars), `language` → returns WAV bytes |
| GET | `/{voice_id}/preview` | streams the original reference WAV |
| DELETE | `/{voice_id}` | delete + clear from any avatars referencing it |

```bash
curl -X POST http://localhost:8000/api/v1/voices/clone -b cookies.txt \
  -F "audio=@sample.wav" -F "name=My Voice" -F "language=en"
```

### Jobs (`/api/v1/jobs`) — clips / podcast / sadtalker pipelines
| Method | Path | Notes |
|---|---|---|
| POST | `/{pipeline}` | `pipeline` ∈ `clips`,`sadtalker`,`podcast`; body `{"params": {...}}` — see §3 for each pipeline's params |
| GET | `/` | list own jobs (`skip`, `limit`) |
| GET | `/{job_id}` | poll for `status`/`progress`/`output`/`error` |

```bash
curl -X POST http://localhost:8000/api/v1/jobs/clips -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"params": {"media_path": "/tmp/videos/input.mp4", "instructions": "find 3 clips about pricing, 30-60s each"}}'
# 201 {"id":"...","pipeline":"clips","status":"pending","progress":0, ...}

curl http://localhost:8000/api/v1/jobs/<job_id> -b cookies.txt
# {"id":"...","status":"done","progress":100,"output":{"clips":[...]}, ...}
```
**Note:** `output.*.file_path` values are raw server-side filesystem paths — there is currently no `GET` endpoint that serves those files to a client (unlike session videos, which have `/sessions/{id}/download-video`). See §7.

### Misc
- `GET /` — service banner
- `GET /health` — DB/Redis/GPU/LLM/STT/TTS readiness (used by Docker healthchecks)
- `GET /docs`, `/redoc`, `/openapi.json` — only when `DEBUG=true`
- `GET /metrics` — Prometheus, when `PROMETHEUS_ENABLED=true`
- `WS /ws/session/{session_id}?token=<jwt>` — real-time chat. Message types in: `audio` (base64), `text`, `stop`/`interrupt`, `set_voice`, `set_language`, `ping`. Message types out: `status`, `transcription`, `token`, `video_chunk_start`, `video_chunk`, `video_chunk_end`, `message`, `tts_fallback`, `interrupted`, `error`, `pong`.

## 5. Docker Services

| Service | Image/Build | Purpose | Queue/Port | Memory limit |
|---|---|---|---|---|
| `postgres` | `postgres:15-alpine` | primary DB | `127.0.0.1:5432` | 1G |
| `redis` | `redis:7-alpine`, `--maxmemory 384mb --maxmemory-policy allkeys-lru` | cache, rate limiter, Celery broker (`/1`) + result backend (`/2`) | `127.0.0.1:6379` | 512M |
| `backend` | `./backend/Dockerfile` (Ubuntu 22.04 + CPU torch 2.6) | FastAPI app, WS chat, REST API | `:8000` | none set |
| `frontend` | `./frontend/Dockerfile` | Next.js 16 UI | `:3000` | none set |
| `celery-worker` | `./backend/Dockerfile` | `-Q cpu,celery`, concurrency 2 — runs `clips`/`podcast` jobs and `cleanup_old_files` beat task | queue: `cpu` | none set |
| `celery-worker-gpu` | `./backend/Dockerfile` | `-Q gpu`, concurrency 1 — runs `sadtalker` jobs | queue: `gpu` | none set |
| `ollama` | `ollama/ollama:latest` | optional local LLM backend (`LLM_PROVIDER=ollama`) | internal only | none set |
| `flower` | `./backend/Dockerfile`, `celery flower --port=5555` | Celery task monitoring UI | `127.0.0.1:5555` | 256M |
| `nginx` | `nginx:alpine` | optional reverse proxy for `/`, `/api/`, `/ws/` | `:80`, `:443` | 128M |
| `sadtalker` | `services/sadtalker/Dockerfile.gpu` **(missing — build will fail)**, profile `gpu` | standalone SadTalker inference microservice | `:5003` | GPU reservation only |

`docker-compose.gpu.yml` (merge overlay, auto-applied by `start.sh` when `nvidia-smi` is detected) adds an NVIDIA GPU reservation to `backend` and `celery-worker` — **not** to `celery-worker-gpu`, which gets no GPU device reservation anywhere in the base `docker-compose.yml` either (only the standalone `sadtalker` service and the `.gpu.yml`/`.prod.yml` overlays request a GPU device for the main app containers). `docker-compose.prod.yml` disables `flower` by default (`profiles: ["dev"]`), removes public DB/Redis ports, and mounts `.env.prod`.

## 6. Environment Variables

Source of truth: `backend/app/config.py` (`Settings`), `.env.example`, `.env.prod.example`. Values below are the code defaults unless marked "no default — required".

| Variable | Controls | Safe default |
|---|---|---|
| `SECRET_KEY` | Generic app secret | **no default — required**, ≥32 chars, rejects known weak placeholders |
| `JWT_SECRET_KEY` | JWT signing key | **no default — required**, ≥32 chars |
| `JWT_ALGORITHM` | JWT algorithm | `HS256` |
| `JWT_EXPIRATION_HOURS` | Token lifetime | `24` |
| `ENVIRONMENT` | `development`/`production` | `development` |
| `DEBUG` | Enables `/docs`, `create_all`, demo-user seeding, verbose 500s | `true` (**must be `false` in prod**) |
| `LOG_LEVEL` | Logger verbosity | `INFO` |
| `DATABASE_URL` / `DATABASE_HOST` / `_PORT` / `_NAME` / `_USER` / `_PASSWORD` | Postgres connection | local `avatar_user`/`password`/`avatar_db` |
| `REDIS_URL` / `REDIS_HOST` / `REDIS_PORT` | Cache + rate-limit backend | `redis://localhost:6379/0` |
| `CELERY_BROKER_URL` | Celery broker (Redis DB 1) | `redis://localhost:6379/1` |
| `CELERY_RESULT_BACKEND` | Celery results (Redis DB 2) | `redis://localhost:6379/2` |
| `USE_LOCAL_STORAGE` | Local FS vs S3 | `true` |
| `LOCAL_STORAGE_PATH` | Local upload dir | `uploads` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` / `S3_BUCKET_NAME` / `CLOUDFRONT_DOMAIN` | S3 storage backend (only when `USE_LOCAL_STORAGE=false`) | empty / `us-east-1` |
| `ANTHROPIC_API_KEY` | Claude API key | empty (health check degrades if `LLM_PROVIDER=anthropic` and unset) |
| `OPENAI_API_KEY` | OpenAI/compatible API key | empty |
| `ELEVENLABS_API_KEY` | Reserved — **not referenced anywhere in `backend/app`** | empty |
| `OPENAI_BASE_URL` | Redirect OpenAI-compatible client (Ollama/vLLM/LM Studio/OpenRouter) | empty → `api.openai.com`; Ollama default `http://localhost:11434/v1` |
| `LLM_PROVIDER` | `anthropic` \| `openai` \| `ollama` | `anthropic` |
| `LLM_MODEL` | Model id | `claude-sonnet-4-6` |
| `LLM_TEMPERATURE` | Sampling temperature | `0.7` |
| `LLM_MAX_TOKENS` | Max output tokens | `2000` |
| `AVATAR_ENGINE` | `musetalk` \| `simple` | `musetalk` |
| `AVATAR_RESOLUTION` | `simple` engine output size (px) | `512` |
| `AVATAR_FPS` | `simple` engine frame rate | `25` |
| `MUSETALK_PATH` | Path to MuseTalk checkout | `models/MuseTalk` |
| `STT_PROVIDER` | Only `whisper` implemented | `whisper` |
| `WHISPER_MODEL` | `tiny`…`large-v3-turbo` | `large-v3-turbo` (compose overrides to `base` for lighter CPU dev) |
| `TTS_PROVIDER` | Only `chatterbox` implemented (`coqui`/`xtts` aliased) | `chatterbox` |
| `TTS_VOICE` | Unused placeholder | `default` |
| `CORS_ORIGINS` | Allowed origins (comma or JSON list) | `localhost:3000,localhost:8000` |
| `AUTH_COOKIE_NAME` | Cookie name | `access_token` |
| `AUTH_COOKIE_SECURE` | `Secure` flag | `false` (**must be `true` behind HTTPS**) |
| `AUTH_COOKIE_SAMESITE` | `lax`\|`strict`\|`none` | `lax` |
| `AUTH_COOKIE_DOMAIN` | Cookie domain | unset |
| `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_PER_HOUR` | Per-identity request caps | `60` / `1000` |
| `WS_MAX_CONNECTIONS` | Declared but **not enforced anywhere in `app/websocket.py`** | `1000` |
| `WS_PING_INTERVAL` / `WS_PING_TIMEOUT` | Declared, **not read by any code path found** | `30` / `10` |
| `MAX_UPLOAD_SIZE` | Declared; avatar upload actually hardcodes its own 10MB cap in `avatars.py` instead of reading this | `10485760` |
| `ALLOWED_EXTENSIONS` | Declared, not enforced (avatars.py checks MIME type, not extension) | `jpg,jpeg,png,webp` |
| `VIDEO_FPS` / `VIDEO_CODEC` / `VIDEO_BITRATE` | Declared; not referenced by `clip_cutter.py`/`podcast_pipeline.py`, which hardcode their own ffmpeg flags | `25` / `h264` / `2000k` |
| `SENTRY_DSN` | Error tracking | empty (disabled) |
| `PROMETHEUS_ENABLED` | `/metrics` endpoint | `true` |
| `OTEL_ENABLED` | OpenTelemetry tracing | `false` |
| `OTEL_SERVICE_NAME` | Span service name | `avatar-backend` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Collector endpoint | unset |
| `FRONTEND_URL` / `BACKEND_URL` | Used to build local file URLs and CORS | `localhost:3000` / `localhost:8000` |
| `SADTALKER_URL` | SadTalker microservice base URL — **not in `Settings`, read via `os.environ` fallback in `sadtalker_engine.py`; present in local `.env` but not forwarded to Celery workers in `docker-compose.yml`** | unset → `not_configured` |
| `UVICORN_WORKERS` | compose-only, uvicorn worker count | `4` |

## 7. Production Readiness Checklist

| Item | Status | Notes |
|---|---|---|
| **Auth — is `demo-user` still the fallback?** | **PARTIAL** | Every resource-owning endpoint (`avatars.py`, `sessions.py`, `voices.py`, `jobs.py`, `messages.py`, `conversations.py`) falls back to `_user_id(current_user)` → literal `"demo-user"` when no valid token is present, i.e. **unauthenticated requests are silently served as a shared demo account rather than rejected**. The demo user itself is safe from login (empty `hashed_password`, explicitly rejected in `login()`), and it's only auto-seeded when `DEBUG=true`. But nothing stops an unauthenticated client from hitting `/api/v1/avatars/upload`, `/api/v1/jobs/clips`, etc. and having it silently attributed to `demo-user` in *any* environment, including `DEBUG=false` in prod, where `demo-user` won't even exist in the DB — those writes would then hit a Postgres FK violation. There is no middleware enforcing "auth required" globally; `require_current_user` exists but most routers use the softer `get_current_user`. |
| **Download / file exposure — are output paths accessible to clients?** | **MISSING** | Clip/podcast/sadtalker job outputs write to `/tmp/videos/...` (or the `video_cache` Docker volume) and are returned in `job.output.*.file_path` as **raw server filesystem paths** — there is no endpoint that serves them over HTTP. Only the live chat pipeline (`storage_service.upload_file` + `serving_url`) and session video export (`/sessions/{id}/download-video`, which reads a *different* hardcoded path `/app/uploads/videos/{session_id}`) expose files to clients. A caller of `POST /jobs/clips` today has no way to retrieve the cut clips via the API. |
| **GPU inference — what needs a real GPU box?** | Documented, not automated | MuseTalk (`musetalk_worker.py`) is CPU-usable but "minutes, not seconds" per sentence — unusable for live chat without a GPU. The SadTalker server is explicitly CPU-only per its own Dockerfile comments and is a different code path than the `gpu` Docker Compose profile expects (`Dockerfile.gpu`, missing). Chatterbox TTS, faster-whisper (`large-v3-turbo`), and the backend's own PyTorch install are all CPU-capable but far slower without CUDA. `docker-compose.gpu.yml` only wires an NVIDIA device reservation into `backend` and `celery-worker` — `celery-worker-gpu` (which is *named* for GPU work and runs the `sadtalker` job) gets no GPU reservation anywhere in the base compose file. |
| **Model configs — dev-sized vs prod-sized** | Mixed | `.env.example` defaults to `WHISPER_MODEL=large-v3-turbo` (prod-grade) but `docker-compose.yml` overrides it to `WHISPER_MODEL=base` for all backend/worker containers — anyone running via `docker compose up` gets the smaller, faster, lower-accuracy model regardless of `.env`. `.env.prod.example` also sets `WHISPER_MODEL=base`. `LLM_MODEL` defaults to `claude-sonnet-4-6` (current, reasonable). `AVATAR_RESOLUTION=512` is a reasonable default for both dev and prod. |
| **Error handling gaps** | PARTIAL | Most REST routes catch and log exceptions cleanly with proper HTTP codes. Celery `run_pipeline_job` catches broadly and stores `job.error`, which is good — but the job never retries (`bind=True` with no `max_retries`/`self.retry()` call, unlike `process_avatar_task`/`generate_video_task` which do retry). The global `Exception` handler in `main.py` leaks the raw exception string in the response body whenever `DEBUG=true` — fine for dev, but a footgun if `DEBUG` is ever accidentally left on in a prod-like environment. WebSocket handlers generally degrade gracefully (TTS fallback chain, MuseTalk→simple fallback, per-chunk try/except so one bad sentence doesn't kill the turn). |
| **Missing tests** | PARTIAL | `backend/tests/` (11 files, ~1400 lines) covers users/auth, avatars, sessions, conversations, websocket connect/auth/e2e, health, and general "regression"/"fallback" cases. **There is no test file covering `jobs.py`, `clip_detector.py`, `clip_cutter.py`, `podcast_pipeline.py`, or `sadtalker_engine.py`** — i.e. the entire async pipeline system (§3.1-3.3) has zero automated test coverage. `voices.py` also has no dedicated test file. No frontend tests exist at all (`frontend/package.json` has no `test` script or test runner dependency). |
| **Secrets management** | PARTIAL | `.env` is correctly gitignored and pydantic validates `SECRET_KEY`/`JWT_SECRET_KEY` are ≥32 chars and not a known placeholder — good baseline hygiene. However: secrets are passed as plain environment variables through `docker-compose.yml` (visible via `docker inspect`, process environment, and `.env` file at rest) rather than a secrets manager (AWS Secrets Manager, Vault, Docker secrets); there's no key-rotation story (JWTs are stateless with no revocation list, so a compromised `JWT_SECRET_KEY` invalidates *every* session on rotation); `docker-compose.prod.yml` correctly drops public `postgres`/`redis` ports but doesn't otherwise change how secrets are injected. |

## 8. How Far From Production

The **live chat pipeline** (register/login → avatar upload → voice clone → real-time WebSocket conversation with STT→LLM→TTS→MuseTalk lip-sync, with graceful degradation at every stage) is the most mature part of the system: it has real fallback chains (TTS: Chatterbox → Edge TTS → gTTS; animation: MuseTalk → static-image ffmpeg), per-tenant ownership checks, rate limiting, security headers, and the best test coverage in the repo. That path is realistically close to a first customer, modulo standing up a real GPU box (MuseTalk on CPU is not viable for live conversation) and fixing the demo-user auth fallback so unauthenticated traffic is rejected instead of silently pooled onto one account (~1-2 days).

The **background job pipelines** (clips, podcast, sadtalker) are architecturally sound — LLM-driven transcript analysis, sentence-snapped clip boundaries, chapter rendering with title cards and burned captions — but are functionally unfinished as a *product feature*: there's no way for a client to upload media into the pipeline (jobs require a pre-existing server-side `media_path`) and no way to download the resulting clips/podcast/avatar video once the job completes (~2-3 days to add an upload endpoint + a `GET /jobs/{id}/download/{artifact}` route backed by `storage_service`, plus wiring these paths through S3/CloudFront instead of raw `/tmp`). SadTalker additionally needs its missing `Dockerfile.gpu` written or the compose file corrected to point at the existing CPU-only `Dockerfile`, and `SADTALKER_URL` needs to be forwarded to the Celery GPU worker's environment in `docker-compose.yml` (~half a day, but currently a silent no-op in Docker deployments that will confuse anyone who set the URL in `.env` and can't figure out why every sadtalker job comes back `not_configured`). None of these three pipelines has automated test coverage, so "wired but untested" is the honest label — expect to spend real time debugging edge cases (malformed LLM JSON, timestamp drift, missing fonts for caption burn-in) the first time each runs against real customer media.

Overall: this is a strong prototype/demo-day system with one production-adjacent feature (live avatar chat) and several well-architected-but-incomplete background pipelines. Rough total effort to first paying customer: **1-2 weeks** — GPU provisioning + auth hardening for the chat path (days 1-3), job-pipeline upload/download endpoints and SadTalker config fixes (days 3-6), and a pass of integration testing against real media for clips/podcast/sadtalker before trusting them unattended (days 6-10+, pipeline-complexity dependent).
