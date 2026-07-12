"""
Persistent MuseTalk worker — loads models once, then processes jobs via stdin/stdout JSON.

Protocol:
  1. Parent sends init config (JSON line) with model paths + use_float16.
  2. Worker loads models, prints "READY\n" to stdout.
  3. Parent sends job lines: {"image": ..., "audio": ..., "output": ..., "coord_cache": ...}
  4. Worker processes, prints {"status": "ok"} or {"status": "error", "msg": "..."} per job.

Logging:
  - Log file defaults to logs/musetalk_worker.log next to this script.
  - Override with env var:  MUSETALK_LOG=/path/to/worker.log
  - Override log level with: MUSETALK_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR  (default INFO)
  - Logs rotate at 10 MB, keeping 5 backups.
"""

import json
import logging
import logging.handlers
import os
import pickle
import subprocess
import sys
import time

import cv2
import numpy as np
import torch
from transformers import WhisperModel

# --------------------------------------------------------------------------- #
# Logging setup — must happen before any other code so every import can log.
# Writes to a rotating file; stdout is reserved for the JSON protocol.
# --------------------------------------------------------------------------- #
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_log_path    = os.environ.get("MUSETALK_LOG", os.path.join(SCRIPT_DIR, "logs", "musetalk_worker.log"))
_log_level   = os.environ.get("MUSETALK_LOG_LEVEL", "INFO").upper()

os.makedirs(os.path.dirname(_log_path), exist_ok=True)

_handler = logging.handlers.RotatingFileHandler(
    _log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_handler.setFormatter(logging.Formatter(
    "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))

logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    handlers=[_handler],
)

log = logging.getLogger("musetalk.worker")

# --------------------------------------------------------------------------- #
# MuseTalk path setup
# --------------------------------------------------------------------------- #
MUSETALK_ROOT = os.path.dirname(SCRIPT_DIR)
if MUSETALK_ROOT not in sys.path:
    sys.path.insert(0, MUSETALK_ROOT)

from musetalk.utils.blending import get_image
from musetalk.utils.face_parsing import FaceParsing
from musetalk.utils.audio_processor import AudioProcessor
from musetalk.utils.utils import datagen, load_all_model
from musetalk.utils.preprocessing import get_landmark_and_bbox, read_imgs, coord_placeholder


# --------------------------------------------------------------------------- #
# Model loading
# --------------------------------------------------------------------------- #

def load_models(config: dict):
    """Load all MuseTalk models from config dict."""
    log.info("=== load_models: starting ===")
    log.debug("Config received: %s", json.dumps({k: v for k, v in config.items() if k != "api_key"}))

    # Redirect stdout → stderr during loading so only protocol messages hit stdout.
    real_stdout = sys.stdout
    sys.stdout  = sys.stderr

    t0 = time.perf_counter()
    try:
        # PyTorch 2.6+ changed torch.load default to weights_only=True, which
        # breaks loading legacy .tar checkpoints. Patch it globally.
        _original_torch_load = torch.load
        def _patched_load(*args, **kwargs):
            kwargs.setdefault("weights_only", False)
            return _original_torch_load(*args, **kwargs)
        torch.load = _patched_load
        log.debug("torch.load patched for weights_only=False compatibility")

        device      = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        use_float16 = config.get("use_float16", False) and device.type == "cuda"
        log.info("Device: %s  |  float16: %s", device, use_float16)

        if device.type == "cuda":
            log.info(
                "GPU: %s  |  VRAM total: %.1f GB  |  VRAM free: %.1f GB",
                torch.cuda.get_device_name(0),
                torch.cuda.get_device_properties(0).total_memory / 1e9,
                (torch.cuda.get_device_properties(0).total_memory - torch.cuda.memory_allocated(0)) / 1e9,
            )

        log.info("Loading VAE / UNet / PE from: %s", config.get("unet_model_path"))
        vae, unet, pe = load_all_model(
            unet_model_path=config["unet_model_path"],
            vae_type=config["vae_type"],
            unet_config=config["unet_config"],
            device=device,
        )
        timesteps = torch.tensor([0], device=device)
        log.debug("VAE / UNet / PE loaded")

        if use_float16:
            log.debug("Casting models to float16")
            pe        = pe.half()
            vae.vae   = vae.vae.half()
            unet.model = unet.model.half()

        pe.to(device)
        vae.vae.to(device)
        unet.model.to(device)
        log.debug("Models moved to %s", device)

        log.info("Loading Whisper from: %s", config.get("whisper_dir"))
        audio_processor = AudioProcessor(feature_extractor_path=config["whisper_dir"])
        weight_dtype    = unet.model.dtype
        whisper         = WhisperModel.from_pretrained(config["whisper_dir"])
        whisper         = whisper.to(device=device, dtype=weight_dtype).eval()
        whisper.requires_grad_(False)
        log.debug("Whisper loaded  dtype=%s", weight_dtype)

        log.info("Loading FaceParsing model")
        fp = FaceParsing()
        log.debug("FaceParsing ready")

    finally:
        sys.stdout = real_stdout

    elapsed = time.perf_counter() - t0
    log.info("=== load_models: done in %.2f s ===", elapsed)

    return {
        "device":          device,
        "vae":             vae,
        "unet":            unet,
        "pe":              pe,
        "timesteps":       timesteps,
        "audio_processor": audio_processor,
        "whisper":         whisper,
        "weight_dtype":    weight_dtype,
        "fp":              fp,
        "use_float16":     use_float16,
    }


# --------------------------------------------------------------------------- #
# FFmpeg command builder — GPU uses NVENC, CPU falls back to libx264
# --------------------------------------------------------------------------- #

def build_ffmpeg_cmd(output_path: str, width: int, height: int, fps: int, audio_path: str, use_gpu: bool = False) -> list:
    """
    Build the FFmpeg command for video encoding.

    - GPU path: NVENC h264 (h264_nvenc) — fast hardware encoding
    - CPU path: libx264 software encoding — works everywhere
    - yuv420p requires even dimensions; caller must ensure width/height are even.
    - -movflags +faststart makes the output web-streamable before full download.
    """
    if use_gpu:
        video_codec_args = [
            "-c:v",    "h264_nvenc",
            "-gpu",    "0",
            "-preset", "p4",
            "-tune",   "hq",
            "-rc",     "vbr",
            "-cq",     "23",
            "-b:v",    "0",
        ]
    else:
        video_codec_args = [
            "-c:v",    "libx264",
            "-preset", "fast",
            "-crf",    "23",
        ]

    return [
        "/usr/local/bin/ffmpeg", "-y",
        "-v", "error",
        # ---- input: raw BGR frames piped from Python ----
        "-f",       "rawvideo",
        "-pix_fmt", "bgr24",
        "-s",       f"{width}x{height}",
        "-r",       str(fps),
        "-i",       "-",
        # ---- input: audio file ----
        "-i",       audio_path,
        # ---- video encoding ----
        *video_codec_args,
        "-pix_fmt", "yuv420p",
        # ---- audio encoding ----
        "-c:a",     "aac",
        "-b:a",     "192k",
        # ---- muxing ----
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]


# --------------------------------------------------------------------------- #
# Job processing
# --------------------------------------------------------------------------- #

@torch.no_grad()
def process_job(job: dict, models: dict) -> dict:
    """
    Run lip-sync inference for one image+audio pair, streaming frames
    directly into FFmpeg via pipe (avoids accumulating all frames in RAM).
    """
    job_id = job.get("job_id", os.path.basename(job.get("output", "unknown")))
    log.info("--- job [%s] started ---", job_id)
    log.debug("Job params: image=%s  audio=%s  output=%s  coord_cache=%s",
              job.get("image"), job.get("audio"), job.get("output"), job.get("coord_cache"))

    # Suppress stdout from MuseTalk internals (tqdm, print) during inference
    real_stdout = sys.stdout
    sys.stdout  = sys.stderr

    t_job = time.perf_counter()

    try:
        image_path  = job["image"]
        audio_path  = job["audio"]
        output_path = job["output"]
        coord_cache = job.get("coord_cache")

        device          = models["device"]
        vae             = models["vae"]
        unet            = models["unet"]
        pe              = models["pe"]
        timesteps       = models["timesteps"]
        audio_processor = models["audio_processor"]
        whisper         = models["whisper"]
        weight_dtype    = models["weight_dtype"]
        fp              = models["fp"]

        use_gpu    = device.type == "cuda"
        fps        = 25
        batch_size = 4

        # ------------------------------------------------------------------ #
        # 1. Audio features
        # ------------------------------------------------------------------ #
        log.info("[%s] extracting audio features from: %s", job_id, audio_path)
        t = time.perf_counter()
        whisper_input_features, librosa_length = audio_processor.get_audio_feature(audio_path)
        whisper_chunks = audio_processor.get_whisper_chunk(
            whisper_input_features,
            device,
            weight_dtype,
            whisper,
            librosa_length,
            fps=fps,
        )
        log.info("[%s] audio features ready — %d chunks  (%.2f s)", job_id, len(whisper_chunks), time.perf_counter() - t)

        # ------------------------------------------------------------------ #
        # 2. Face coordinates (use cache when available)
        # ------------------------------------------------------------------ #
        input_img_list = [image_path]

        if coord_cache and os.path.exists(coord_cache):
            log.info("[%s] loading coord cache from: %s", job_id, coord_cache)
            with open(coord_cache, "rb") as f:
                coord_list = pickle.load(f)
            frame_list = read_imgs(input_img_list)
            log.debug("[%s] coord cache loaded — %d coords", job_id, len(coord_list))
        else:
            log.info("[%s] running landmark + bbox detection", job_id)
            t = time.perf_counter()
            coord_list, frame_list = get_landmark_and_bbox(input_img_list, 0)
            log.info("[%s] detection done — %d faces found  (%.2f s)", job_id, len(coord_list), time.perf_counter() - t)
            if coord_cache:
                cache_dir = os.path.dirname(coord_cache)
                if cache_dir:
                    os.makedirs(cache_dir, exist_ok=True)
                with open(coord_cache, "wb") as f:
                    pickle.dump(coord_list, f)
                log.debug("[%s] coord cache saved to: %s", job_id, coord_cache)

        if not frame_list or all(c == coord_placeholder for c in coord_list):
            log.error("[%s] no face detected in image: %s", job_id, image_path)
            return {"status": "error", "msg": "No face detected in image"}

        # ------------------------------------------------------------------ #
        # 3. Encode input face crops → latents
        # ------------------------------------------------------------------ #
        log.info("[%s] encoding %d face crop(s) to latents", job_id, len(frame_list))
        t = time.perf_counter()
        input_latent_list = []
        for bbox, frame in zip(coord_list, frame_list):
            if bbox == coord_placeholder:
                log.debug("[%s] skipping placeholder bbox", job_id)
                continue
            x1, y1, x2, y2 = bbox
            crop_frame = frame[y1:y2, x1:x2]
            crop_frame = cv2.resize(crop_frame, (256, 256), interpolation=cv2.INTER_LANCZOS4)
            latents    = vae.get_latents_for_unet(crop_frame)
            input_latent_list.append(latents)

        if not input_latent_list:
            log.error("[%s] failed to encode any face latents", job_id)
            return {"status": "error", "msg": "Failed to encode face latents"}

        log.info("[%s] %d latent(s) encoded  (%.2f s)", job_id, len(input_latent_list), time.perf_counter() - t)

        # Cycle frames for smooth looping
        frame_list_cycle  = frame_list         + frame_list[::-1]
        coord_list_cycle  = coord_list         + coord_list[::-1]
        latent_list_cycle = input_latent_list  + input_latent_list[::-1]
        log.debug("[%s] cycle length: %d frames", job_id, len(frame_list_cycle))

        # ------------------------------------------------------------------ #
        # 4. Output directory
        # ------------------------------------------------------------------ #
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        # ------------------------------------------------------------------ #
        # 5. Even frame dimensions (yuv420p requirement)
        # ------------------------------------------------------------------ #
        raw_h, raw_w, _ = frame_list_cycle[0].shape
        width  = raw_w if raw_w % 2 == 0 else raw_w - 1
        height = raw_h if raw_h % 2 == 0 else raw_h - 1
        if width != raw_w or height != raw_h:
            log.debug("[%s] dimensions adjusted %dx%d → %dx%d for yuv420p", job_id, raw_w, raw_h, width, height)
        log.info("[%s] output frame size: %dx%d @ %d fps  |  encoder: %s",
                 job_id, width, height, fps, "h264_nvenc" if use_gpu else "libx264")

        # ------------------------------------------------------------------ #
        # 6. Batch inference → pipe directly into FFmpeg
        # ------------------------------------------------------------------ #
        cmd = build_ffmpeg_cmd(output_path, width, height, fps, audio_path, use_gpu=use_gpu)
        log.info("[%s] launching FFmpeg: %s", job_id, " ".join(cmd))

        gen = datagen(
            whisper_chunks=whisper_chunks,
            vae_encode_latents=latent_list_cycle,
            batch_size=batch_size,
            delay_frame=0,
            device=device,
        )

        process    = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        frame_idx  = 0
        batch_idx  = 0
        skipped    = 0
        t_infer    = time.perf_counter()

        log.info("[%s] inference + pipe loop starting  (batch_size=%d)", job_id, batch_size)

        try:
            for whisper_batch, latent_batch in gen:
                whisper_batch       = whisper_batch.to(device)
                audio_feature_batch = pe(whisper_batch)
                latent_batch        = latent_batch.to(device=device, dtype=unet.model.dtype)

                pred_latents = unet.model(
                    latent_batch, timesteps, encoder_hidden_states=audio_feature_batch
                ).sample
                recon = vae.decode_latents(pred_latents)

                for res_frame in recon:
                    bbox      = coord_list_cycle[frame_idx % len(coord_list_cycle)]
                    ori_frame = frame_list_cycle[frame_idx % len(frame_list_cycle)].copy()
                    x1, y1, x2, y2 = bbox

                    try:
                        res_frame     = cv2.resize(res_frame.astype(np.uint8), (x2 - x1, y2 - y1))
                        combine_frame = get_image(ori_frame, res_frame, [x1, y1, x2, y2], fp=fp)
                        combine_frame = combine_frame[:height, :width]
                        process.stdin.write(combine_frame.tobytes())
                    except Exception as frame_err:
                        skipped += 1
                        log.warning("[%s] frame %d skipped: %s", job_id, frame_idx, frame_err)

                    frame_idx += 1

                batch_idx += 1
                if batch_idx % 10 == 0:
                    elapsed  = time.perf_counter() - t_infer
                    fps_real = frame_idx / elapsed if elapsed > 0 else 0
                    log.info("[%s] batch %d | frames piped: %d | skipped: %d | %.1f fps",
                             job_id, batch_idx, frame_idx, skipped, fps_real)

        except Exception as e:
            log.exception("[%s] inference loop failed at frame %d: %s", job_id, frame_idx, e)
            try:
                process.stdin.close()
            except OSError:
                pass
            process.kill()
            process.stderr.read()
            process.wait()
            raise

        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
            stderr_bytes = process.stderr.read()
            process.wait()

        infer_elapsed = time.perf_counter() - t_infer
        log.info("[%s] inference done — %d frames piped, %d skipped  (%.2f s, avg %.1f fps)",
                 job_id, frame_idx, skipped, infer_elapsed, frame_idx / infer_elapsed if infer_elapsed > 0 else 0)

        if process.returncode != 0:
            ffmpeg_err = stderr_bytes.decode(errors="replace").strip()
            log.error("[%s] FFmpeg failed (exit %d):\n%s", job_id, process.returncode, ffmpeg_err)
            return {
                "status": "error",
                "msg": f"FFmpeg failure (exit {process.returncode}): {ffmpeg_err}",
            }

        total_elapsed = time.perf_counter() - t_job
        log.info("[%s] job complete — output: %s  (total %.2f s)", job_id, output_path, total_elapsed)
        return {"status": "ok"}

    except Exception as e:
        log.exception("[%s] unhandled exception in process_job", job_id)
        return {"status": "error", "msg": str(e)}

    finally:
        sys.stdout = real_stdout


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #

def main():
    """Main worker loop: reads JSON lines from stdin, writes JSON lines to stdout."""
    log.info("========================================")
    log.info("MuseTalk worker starting  PID=%d", os.getpid())
    log.info("Log file : %s  (level=%s)", _log_path, _log_level)
    log.info("Python   : %s", sys.version.split()[0])
    log.info("Torch    : %s", torch.__version__)
    log.info("CUDA available: %s", torch.cuda.is_available())
    if torch.cuda.is_available():
        log.info("CUDA version : %s  |  GPU: %s", torch.version.cuda, torch.cuda.get_device_name(0))
    log.info("========================================")

    models = None

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            log.error("Invalid JSON received: %s  |  raw: %.120s", e, line)
            print(json.dumps({"status": "error", "msg": "Invalid JSON input"}), flush=True)
            continue

        # ---- Step 1: first message is always the init config ----
        if models is None:
            log.info("Received init config — loading models ...")
            try:
                models = load_models(data)
                log.info("Models loaded successfully — sending READY")
                print("READY", flush=True)
            except Exception as e:
                log.exception("Model loading failed: %s", e)
                print(
                    json.dumps({"status": "error", "msg": f"Failed initialization: {str(e)}"}),
                    flush=True,
                )
                sys.exit(1)

        # ---- Step 2: subsequent messages are job requests ----
        else:
            log.debug("Received job: %.200s", line)
            result = process_job(data, models)
            log.debug("Job result: %s", result)
            print(json.dumps(result), flush=True)

    log.info("stdin closed — worker exiting cleanly")


if __name__ == "__main__":
    main()
