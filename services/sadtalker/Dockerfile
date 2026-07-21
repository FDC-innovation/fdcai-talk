# Self-hosted SadTalker, CPU-only. Runs natively in Docker on the M1 (Linux
# arm64 container, no GPU/MPS needed since we're deliberately staying on
# CPU per the M1 constraint - just slow, not broken).
#
# NOTE: this build is HEAVY - cloning the repo, installing torch + a large
# CV/ML requirements.txt, and downloading several GB of checkpoints. First
# build will likely take 20-40+ minutes depending on connection speed.
# Subsequent builds are cached unless requirements.txt changes upstream.
FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git wget ffmpeg libgl1 libglib2.0-0 build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Canonical repo as of the license move to Apache 2.0 - confirm this hasn't
# moved again before relying on it long-term.
RUN git clone https://github.com/OpenTalker/SadTalker.git .

# CPU-only torch build. The repo's own docs assume CUDA (torch+cu113) -
# deliberately overriding that since there's no CUDA here.
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# NOTE: this requirements.txt is the repo's own and may pull in packages
# with native builds (dlib, numba, etc.) that can be slow or fragile to
# compile on ARM. If this step fails, the error will name the specific
# package - check for an ARM-compatible wheel or a version pin issue.
RUN pip install --no-cache-dir -r requirements.txt

# Known compatibility break: basicsr (a gfpgan dependency, imported
# unconditionally by SadTalker even though we never use --enhancer) imports
# torchvision.transforms.functional_tensor, which newer torchvision versions
# removed - the function moved to torchvision.transforms.functional instead.
# Patch the installed package directly rather than pinning an old torchvision
# (which would risk other compatibility issues with newer torch).
RUN sed -i 's/from torchvision.transforms.functional_tensor import rgb_to_grayscale/from torchvision.transforms.functional import rgb_to_grayscale/' \
    /usr/local/lib/python3.10/site-packages/basicsr/data/degradations.py

# Official checkpoint download script - fetches several GB of model weights.
RUN bash scripts/download_models.sh

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt
COPY server.py .

EXPOSE 5003
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5003"]
