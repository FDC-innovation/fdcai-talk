# Chalchitra — Setup & Run (GPU)

Simple steps to clone, configure, and run the app with the UI.
Aditya will share the `.env` file separately — place it in the project root.

**Needs:** Git, Docker + Docker Compose, and NVIDIA GPU drivers with the NVIDIA Container Toolkit.

---

### 1. Clone

```
git clone https://github.com/FDC-innovation/fdcai-talk.git
cd fdcai-talk
git checkout feature/final-demo
```

### 2. Add the environment file

Put the `.env` (shared by Aditya) in the project root, next to `docker-compose.yml`.
Make sure these three are set for GPU:

| Setting | Value |
|---|---|
| `TTS_FORCE_EDGE` | remove it or set `0` |
| `AVATAR_ENGINE` | `sadtalker` |
| `SADTALKER_URL` | `http://sadtalker:5003` |

### 3. Start the stack

```
docker compose up -d
```

First run downloads models — give it a few minutes. Then check status:

```
docker compose ps
```

Make sure **ollama** is running (the scripting step needs it).

### 4. Start the avatar engine

```
docker compose --profile cpu-avatar up -d sadtalker
curl -sf http://localhost:5003/health
```

You should get `{"status":"ok"}`.

### 5. Pull the language model (first time only)

```
docker compose exec ollama ollama pull llama3.2:1b
```

### 6. Open the UI

```
http://localhost:3000
```

Login: `exp2@example.com` / `TestPass123` (or register a new account).

### 7. Make a video

1. **Avatars** tab -> upload a clear front-facing photo -> name it.
2. **Video** tab -> upload a short deck video -> pick the presenter.
3. (Optional) toggle **Clone a voice** -> upload a 10-60s clip or pick a saved voice.
4. Choose a language -> **Generate** -> watch the stages -> preview & download.

On GPU the clips come back lip-synced and render fast.

---

### Quick reference

| What | Where |
|---|---|
| UI | `http://localhost:3000` |
| Backend API | `http://localhost:8000` (`/api/v1`) |
| Avatar engine | `http://localhost:5003/health` |
| Test user | `exp2@example.com` / `TestPass123` |

### If something breaks

- **Job fails at ~10% ("Connection error")** -> ollama isn't running. `docker compose start ollama`, retry.
- **No lip-sync** -> check `AVATAR_ENGINE=sadtalker` and `SADTALKER_URL=http://sadtalker:5003`, then `docker compose up -d backend celery-worker-gpu`.
- **Voice cloning not applied** -> make sure `TTS_FORCE_EDGE` is off, then `docker compose up -d celery-worker-gpu`.
- After any `.env` change: `docker compose up -d backend celery-worker-gpu`.
