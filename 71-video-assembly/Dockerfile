FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# ffmpeg encodes the video (libx264 + aac); DejaVu is the frame font.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Piper voice, fetched once at image build time from the official rhasspy/piper-voices
# repository on Hugging Face, pinned to its v1.0.0 revision. License: see the voice's
# MODEL_CARD in that repository. Build with --build-arg PIPER_VOICE_NAME=... for another
# voice from the same repo (the path mirrors lang/lang_REGION/name/quality).
ARG PIPER_VOICE_BASE=https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0
ARG PIPER_VOICE_PATH=en/en_US/lessac/medium
ARG PIPER_VOICE_NAME=en_US-lessac-medium
ADD ${PIPER_VOICE_BASE}/${PIPER_VOICE_PATH}/${PIPER_VOICE_NAME}.onnx /voices/${PIPER_VOICE_NAME}.onnx
ADD ${PIPER_VOICE_BASE}/${PIPER_VOICE_PATH}/${PIPER_VOICE_NAME}.onnx.json /voices/${PIPER_VOICE_NAME}.onnx.json
RUN chmod 644 /voices/*
ENV PIPER_VOICE=/voices/${PIPER_VOICE_NAME}.onnx TTS_BACKEND=piper DATA_DIR=/data

COPY app ./app
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown appuser /data
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
