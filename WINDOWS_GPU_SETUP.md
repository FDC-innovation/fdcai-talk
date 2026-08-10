# Chalchitra

AI avatar video generator: upload a deck/audio walkthrough, get back a
short, lip-synced presenter video. Pipeline: transcribe (Whisper) → write
short spoken sections (LLM) → synthesize voice (Chatterbox/Edge TTS) →
animate + lip-sync (SadTalker) → assemble final clip.

This guide covers a **Windows + WSL2 + NVIDIA GPU** setup, tested end-to-end
including modern GPUs (RTX 50-series / Blackwell). Follow it in order —
every step below reflects lessons learned getting this running cleanly, so
following it as written should get you a working GPU setup on the first
attempt.

## Prerequisites

- Windows 10/11 with an NVIDIA GPU
- ~20GB free disk space (model checkpoints + Docker images)
- A stable internet connection (initial build downloads several GB)

## 1. Install WSL2

PowerShell (as Administrator):
```powershell
wsl --install
```
Restart when prompted. Ubuntu installs automatically on reboot (set a
username/password if asked).

Verify:
```powershell
wsl --status
```
Should show `Default Version: 2`.

> If you see `ERROR_ALREADY_EXISTS`, WSL/Ubuntu is already installed — just
> run `wsl --status` to confirm and move on.

## 2. Install the NVIDIA driver (Windows side)

Install the latest GeForce/Studio driver for your GPU from nvidia.com. Do
**not** install a CUDA toolkit inside WSL — the Windows driver provides GPU
access to WSL automatically.

Verify:
```powershell
nvidia-smi
```
Your GPU should be listed.

## 3. Install Docker Desktop

Install from docker.com.
- Settings → General → enable **"Use the WSL 2 based engine"**
- Settings → Resources → WSL Integration → enable your Ubuntu distro
- Restart Docker Desktop

Verify GPU passthrough:
```powershell
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi
```
Your GPU should show up inside the container output. If this fails, update
Docker Desktop and re-check the WSL2 engine / integration settings before
continuing.

## 4. Get the code

Open the **Ubuntu** app (Start menu → search "Ubuntu" — not PowerShell),
then:
```bash
git clone https://github.com/FDC-innovation/fdcai-talk.git
cd fdcai-talk
git checkout feature/final-demo   # or your working branch
```
Work inside the WSL filesystem (`~/fdcai-talk`) — **not** `/mnt/c/...` —
building on the Windows-mounted path is slow and causes file-watch issues.

## 5. Create `.env`

Create `.env` in the project root:
```env
POSTGRES_USER=avatar_user
POSTGRES_PASSWORD=change_me_strong
POSTGRES_DB=avatar_db
DATABASE_URL=postgresql://avatar_user:change_me_strong@postgres:5432/avatar_db
JWT_SECRET_KEY=change_me_long_random
SECRET_KEY=change_me_long_random

AVATAR_ENGINE=sadtalker
SADTALKER_URL=http://sadtalker:5003

LLM_PROVIDER=ollama
LLM_MODEL=llama3.2:1b
OPENAI_BASE_URL=http://ollama:11434/v1

NEXT_PUBLIC_API_URL=http://localhost:8000
FRONTEND_URL=http://localhost:3000
BACKEND_URL=http://localhost:8000
```
**`POSTGRES_PASSWORD` must match the password inside `DATABASE_URL`
exactly.** URL-encode special characters (`@ : / #`) in `DATABASE_URL` if
your password contains them.

Confirm `frontend/.dockerignore` exists and contains:

node_modules
.next

This file (and a matching volume exclusion in `docker-compose.yml`, already
present on this branch) prevents the frontend's dev bind-mount from
overwriting the production `.next` build produced during the image build.
Without it, the frontend container starts but immediately errors with
*"Could not find a production build in the '.next' directory."*

## 6. Build & start the stack

```bash
docker compose up -d --build
```
First build downloads CUDA torch + model checkpoints (several GB) — give it
15–30 minutes depending on your connection.

Then start the avatar engine (gated behind a profile flag) and pull the LLM:
```bash
docker compose --profile cpu-avatar up -d sadtalker
docker compose exec ollama ollama pull llama3.2:1b
```

Check everything is up:
```bash
docker compose ps
```
All services should show healthy except `avatar-flower`, which restart-loops
harmlessly (it's just the Celery monitoring dashboard — unrelated to the
pipeline, safe to ignore).

> **If `celery-worker` / `celery-worker-gpu` show "unhealthy" right after
> `docker compose up -d`:** this is a WSL2 Docker networking DNS race — the
> container can't resolve `redis` for the first ~30-60s after startup. It
> retries automatically and recovers on its own (check with `docker compose
> logs celery-worker --tail 15` — it'll end with `celery@... ready.` once
> resolved). Just re-run `docker compose up -d` once it settles.

## 7. Confirm your GPU is actually usable for lip-sync

**This step matters more than it looks.** SadTalker's Dockerfile installs a
CUDA-specific PyTorch build. If your GPU is newer than what that build
supports, every generation will *silently fall back* to a static image with
audio — no error shown in the UI, no lip-sync, and nothing obviously wrong
in `docker compose ps`.

Check your GPU's compute capability is supported:
```bash
docker compose exec sadtalker python3 -c \
  "import torch; print('capability:', torch.cuda.get_device_capability(0)); print('supported:', torch.cuda.get_arch_list())"
```
Your GPU's capability tuple must appear in the printed `supported` list. As
of this branch, the Dockerfile targets **CUDA 12.8**, which covers RTX
30/40/50-series GPUs (`sm_86` through `sm_120`, including Blackwell). If
your GPU is even newer and its capability is missing from the list, bump
`services/sadtalker/Dockerfile.gpu`:
```dockerfile
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04   # → bump if needed
...
RUN pip install --no-cache-dir --timeout 120 --retries 10 torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu128   # → matching cuXXX index
```
then rebuild: `docker compose --profile cpu-avatar up -d --build sadtalker`.
Check https://pytorch.org/get-started/locally/ for the latest wheel index
matching your GPU generation.

## 8. Open the app

http://localhost:3000

Login: `exp2@example.com` / `TestPass123` (or register).

Video tab → upload a short deck → select the presenter → **Generate**. On a
working GPU setup, the lip-synced clip renders in well under a minute.

---

## Troubleshooting

**`docker run --gpus all` fails** → GPU passthrough isn't set up. Confirm:
latest NVIDIA Windows driver, Docker Desktop on the WSL2 engine, WSL
integration enabled for your distro. Restart Docker Desktop and retry.

**Frontend build fails / `.next` error** → `frontend/.dockerignore` is
missing or the `docker-compose.yml` frontend `volumes` block is missing the
`/app/.next` exclusion (see Step 5). Add it, then:
```bash
docker compose build --no-cache frontend
```

**`DB auth failed for avatar_user`** → `POSTGRES_PASSWORD` doesn't match the
password inside `DATABASE_URL`, or the postgres volume was created earlier
with a different password. On a fresh setup:
```bash
docker compose down -v   # wipes DB data
docker compose up -d
```

**Job fails at ~10% with "Connection error"** → Ollama isn't running:
```bash
docker compose start ollama
```

**Video generates but has no lip-sync (static image + audio only)** →
almost always the GPU-compatibility issue from Step 7. Check the celery-gpu
logs for the real error:
```bash
docker compose logs celery-worker-gpu --tail 60
```
Look for `RuntimeError: CUDA error: no kernel image is available for
execution on the device` — this confirms it's the PyTorch/CUDA build not
supporting your specific GPU architecture; see Step 7 to fix.

**`[explainer_sections] Failed to parse LLM JSON after all repairs"`** →
small local models (like `llama3.2:1b`) don't always follow "respond with
only JSON" instructions reliably. This branch already forces strict JSON
output via Ollama's `response_format: json_object` mode (see
`backend/app/services/llm.py` / `explainer_pipeline.py`), which should
prevent this in most cases. If you still see it, consider switching
`LLM_MODEL` to a larger model (`llama3.1:8b`+) in `.env`, then:
```bash
docker compose exec ollama ollama pull llama3.1:8b
# update LLM_MODEL in .env, then:
docker compose up -d --build backend celery-worker celery-worker-gpu
```

**`sadtalker` shows `unhealthy` in `docker compose ps`** → this can be a lag
in the health check endpoint even when the service works fine. Verify with
a real generation (Step 8) or the manual capability check (Step 7) rather
than trusting the status label alone for this specific service.

**`Chatterbox failed (name 'os' is not defined)` in logs** → known issue in
the Chatterbox TTS integration; the pipeline automatically falls back to
Edge TTS, so voice synthesis still works — just without voice cloning.

**`flower` container restart-looping** → harmless, ignore. It's the Celery
monitoring dashboard and isn't required for video generation.

## Quick reference

| What | Where |
|---|---|
| UI | http://localhost:3000 |
| Backend | http://localhost:8000 (`/api/v1`) |
| SadTalker | http://localhost:5003/health |
| Test user | `exp2@example.com` / `TestPass123` |