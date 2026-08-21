# ==============================================================================
# Dockerfile: STB Smart Voice Assistant & Smart Home Hub (BUDI)
# Target Architecture: Linux ARM64 (Amlogic S905X / STB Armbian) & AMD64 (PC/Mac)
# ==============================================================================

FROM python:3.11-slim-bookworm

# 1. Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Jakarta

# 2. Install essential system dependencies (ALSA, PortAudio, MPV, FFmpeg, build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    alsa-utils \
    libasound2 \
    libasound2-dev \
    libportaudio2 \
    portaudio19-dev \
    pulseaudio-utils \
    mpv \
    ffmpeg \
    curl \
    ca-certificates \
    gcc \
    g++ \
    make \
    git \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# 3. Setup working directory
WORKDIR /app

# 4. Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip wheel setuptools && \
    pip install --no-cache-dir -r requirements.txt

# 5. Copy project source code, models, sounds, and web assets
COPY core/ /app/core/
COPY skills/ /app/skills/
COPY models/ /app/models/
COPY sounds/ /app/sounds/
COPY web/ /app/web/
COPY config.yaml /app/config.yaml
COPY main.py /app/main.py

# 6. Ensure data directory exists
RUN mkdir -p /app/data

# 7. Expose Web Dashboard & Smart Display port
EXPOSE 8080

# 8. Run Assistant BUDI
CMD ["python3", "main.py"]
