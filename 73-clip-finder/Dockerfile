FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# ffmpeg cuts and encodes the clips and burns the captions (Debian's build has libass);
# DejaVu is the caption font; libgl1/libglib2.0-0 are what opencv-python (PySceneDetect) loads.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core libgl1 libglib2.0-0 \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Whisper model, fetched once at image build time from Systran/faster-whisper-small (MIT) on
# Hugging Face, pinned to one commit and checked against its SHA-256. The running service
# never downloads anything (HF_HUB_OFFLINE=1).
ARG WHISPER_REPO=Systran/faster-whisper-small
ARG WHISPER_REVISION=536b0662742c02347bc0e980a01041f333bce120
ARG WHISPER_MODEL_SHA256=3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671
ARG WHISPER_BASE=https://huggingface.co/${WHISPER_REPO}/resolve/${WHISPER_REVISION}
ADD ${WHISPER_BASE}/config.json ${WHISPER_BASE}/tokenizer.json ${WHISPER_BASE}/vocabulary.txt \
    ${WHISPER_BASE}/model.bin /models/faster-whisper-small/
RUN echo "${WHISPER_MODEL_SHA256}  /models/faster-whisper-small/model.bin" | sha256sum -c - \
  && chmod -R a+rX /models
ENV WHISPER_MODEL=/models/faster-whisper-small HF_HUB_OFFLINE=1 DATA_DIR=/data

COPY app ./app
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown appuser /data
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
