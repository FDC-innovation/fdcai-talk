#!/usr/bin/env python3
"""
Persistent MuseTalk worker — loads models once, then processes jobs via stdin/stdout JSON.

Protocol:
  1. Parent sends init config (JSON line) with model paths + use_float16.
  2. Worker loads models, prints "READY\n" to stdout.
  3. Parent sends job lines: {"image": ..., "audio": ..., "output": ..., "coord_cache": ...}
  4. Worker processes, prints {"status": "ok"} or {"status": "error", "msg": "..."} per job.
"""

import copy
import glob
import json
import os
import pickle
import sys
import subprocess

import cv2
import numpy as np
import torch
from tqdm import tqdm
from transformers import WhisperModel

# Add parent dir (MuseTalk root) to path so musetalk.* imports work
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MUSETALK_ROOT = os.path.dirname(SCRIPT_DIR)
if MUSETALK_ROOT not in sys.path:
    sys.path.insert(0, MUSETALK_ROOT)

from musetalk.utils.blending import get_image
from musetalk.utils.face_parsing import FaceParsing
from musetalk.utils.audio_processor import AudioProcessor
from musetalk.utils.utils import datagen, load_all_model
from musetalk.utils.preprocessing import get_landmark_and_bbox, read_imgs, coord_placeholder


def load_models(config: dict):
    """Load all MuseTalk models from config dict."""
    # Redirect stdout to stderr during model loading so that only our
    # protocol messages (READY, JSON results) appear on stdout.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    # PyTorch 2.6 changed torch.load default to weights_only=True, which
    # breaks loading legacy .tar checkpoints. Patch it globally.
    _original_torch_load = torch.load
    def _patched_load(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return _original_torch_load(*args, **kwargs)
    torch.load = _patched_load

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    use_float16 = config.get("use_float16", False) and device.type == "cuda"

    vae, unet, pe = load_all_model(
        unet_model_path=config["unet_model_path"],
        vae_type=config["vae_type"],
        unet_config=config["unet_config"],
        device=device,
    )
    timesteps = torch.tensor([0], device=device)

    if use_float16:
        pe = pe.half()
        vae.vae = vae.vae.half()
        unet.model = unet.model.half()

    pe = pe.to(device)
    vae.vae = vae.vae.to(device)
    unet.model = unet.model.to(device)

    audio_processor = AudioProcessor(feature_extractor_path=config["whisper_dir"])
    weight_dtype = unet.model.dtype
    whisper = WhisperModel.from_pretrained(config["whisper_dir"])
    whisper = whisper.to(device=device, dtype=weight_dtype).eval()
    whisper.requires_grad_(False)

    fp = FaceParsing()

    # Restore stdout for protocol communication
    sys.stdout = real_stdout

    return {
        "device": device,
        "vae": vae,
        "unet": unet,
        "pe": pe,
        "timesteps": timesteps,
        "audio_processor": audio_processor,
        "whisper": whisper,
        "weight_dtype": weight_dtype,
        "fp": fp,
        "use_float16": use_float16,
    }


@torch.no_grad()
def process_job(job: dict, models: dict) -> dict:
    """Run lip-sync inference for one image+audio pair."""
    # Suppress stdout from MuseTalk internals (tqdm, print) during inference
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        image_path = job["image"]
        audio_path = job["audio"]
        output_path = job["output"]
        coord_cache = job.get("coord_cache")

        device = models["device"]
        vae = models["vae"]
        unet = models["unet"]
        pe = models["pe"]
        timesteps = models["timesteps"]
        audio_processor = models["audio_processor"]
        whisper = models["whisper"]
        weight_dtype = models["weight_dtype"]
        fp = models["fp"]

        fps = 25
        batch_size = 4

        # Prepare input image
        input_img_list = [image_path]

        # Extract audio features
        whisper_input_features, librosa_length = audio_processor.get_audio_feature(audio_path)
        whisper_chunks = audio_processor.get_whisper_chunk(
            whisper_input_features,
            device,
            weight_dtype,
            whisper,
            librosa_length,
            fps=fps,
        )

        # Get face coordinates (use cache if available)
        if coord_cache and os.path.exists(coord_cache):
            with open(coord_cache, "rb") as f:
                coord_list = pickle.load(f)
            frame_list = read_imgs(input_img_list)
        else:
            coord_list, frame_list = get_landmark_and_bbox(input_img_list, 0)
            if coord_cache:
                os.makedirs(os.path.dirname(coord_cache), exist_ok=True)
                with open(coord_cache, "wb") as f:
                    pickle.dump(coord_list, f)

        if not frame_list or all(c == coord_placeholder for c in coord_list):
            return {"status": "error", "msg": "No face detected in image"}

        # Encode input latents
        input_latent_list = []
        for bbox, frame in zip(coord_list, frame_list):
            if bbox == coord_placeholder:
                continue
            x1, y1, x2, y2 = bbox
            crop_frame = frame[y1:y2, x1:x2]
            crop_frame = cv2.resize(crop_frame, (256, 256), interpolation=cv2.INTER_LANCZOS4)
            latents = vae.get_latents_for_unet(crop_frame)
            input_latent_list.append(latents)

        if not input_latent_list:
            return {"status": "error", "msg": "Failed to encode face latents"}

        # Cycle frames for smooth looping
        frame_list_cycle = frame_list + frame_list[::-1]
        coord_list_cycle = coord_list + coord_list[::-1]
        input_latent_list_cycle = input_latent_list + input_latent_list[::-1]

        # Batch inference
        video_num = len(whisper_chunks)
        gen = datagen(
            whisper_chunks=whisper_chunks,
            vae_encode_latents=input_latent_list_cycle,
            batch_size=batch_size,
            delay_frame=0,
            device=device,
        )

        res_frame_list = []
        total = int(np.ceil(float(video_num) / batch_size))
        for whisper_batch, latent_batch in gen:
            audio_feature_batch = pe(whisper_batch)
            latent_batch = latent_batch.to(dtype=unet.model.dtype)
            pred_latents = unet.model(
                latent_batch, timesteps, encoder_hidden_states=audio_feature_batch
            ).sample
            recon = vae.decode_latents(pred_latents)
            for res_frame in recon:
                res_frame_list.append(res_frame)

        # Compose output frames
        import tempfile
        tmp_frames_dir = tempfile.mkdtemp(prefix="musetalk_frames_")

        for i, res_frame in enumerate(res_frame_list):
            bbox = coord_list_cycle[i % len(coord_list_cycle)]
            ori_frame = copy.deepcopy(frame_list_cycle[i % len(frame_list_cycle)])
            x1, y1, x2, y2 = bbox
            try:
                res_frame = cv2.resize(res_frame.astype(np.uint8), (x2 - x1, y2 - y1))
            except Exception:
                continue
            combine_frame = get_image(ori_frame, res_frame, [x1, y1, x2, y2], fp=fp)
            cv2.imwrite(os.path.join(tmp_frames_dir, f"{i:08d}.png"), combine_frame)

        # Encode to video with ffmpeg
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-v", "fatal",
            "-r", str(fps),
            "-i", os.path.join(tmp_frames_dir, "%08d.png"),
            "-i", audio_path,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        # Cleanup temp frames
        import shutil
        shutil.rmtree(tmp_frames_dir, ignore_errors=True)

        sys.stdout = real_stdout
        return {"status": "ok"}

    except Exception as e:
        sys.stdout = real_stdout
        return {"status": "error", "msg": str(e)}


def main():
    # Step 1: Read init config from stdin
    init_line = sys.stdin.readline().strip()
    if not init_line:
        print(json.dumps({"status": "error", "msg": "No init config received"}), flush=True)
        sys.exit(1)

    config = json.loads(init_line)

    # Step 2: Load models
    try:
        models = load_models(config)
    except Exception as e:
        print(json.dumps({"status": "error", "msg": f"Model load failed: {e}"}), flush=True)
        sys.exit(1)

    # Step 3: Signal ready
    print("READY", flush=True)

    # Step 4: Process jobs from stdin
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
            result = process_job(job, models)
        except Exception as e:
            result = {"status": "error", "msg": str(e)}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
