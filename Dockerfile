# --- UI build -------------------------------------------------------------
FROM node:22-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build

# --- runtime ---------------------------------------------------------------
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-gi python3-gst-1.0 gir1.2-gst-plugins-bad-1.0 \
      gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
      gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-x \
      ffmpeg fontconfig fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN python3 -m venv --system-site-packages /venv \
    && /venv/bin/pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY samples samples
COPY scripts scripts
COPY --from=ui /ui/dist ui/dist

# Sample media is generated at first start if the media volume is empty.
ENV PATH=/venv/bin:$PATH PLAYOUT_MEDIA_DIR=/app/media PLAYOUT_DATA_DIR=/app/data
EXPOSE 8080/tcp 9000/udp 9001/udp 9002/udp 9003/udp
WORKDIR /app/backend
CMD ["sh", "-c", "[ -n \"$(ls -A /app/media 2>/dev/null)\" ] || /app/scripts/make_sample_media.sh /app/media; exec python -m playout"]
