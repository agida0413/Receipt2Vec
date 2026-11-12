FROM python:3.11-slim

# PADDLE_PDX_CACHE_HOME: PaddleOCR 모델 캐시 위치 (docker-compose 에서 볼륨 마운트 → 재다운로드 방지)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PADDLE_PDX_CACHE_HOME=/models/paddlex \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    PORT=3002

# OpenCV 런타임 의존 라이브러리
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# PaddlePaddle CPU 버전을 먼저 설치 (GPU 사용 시 paddlepaddle-gpu 로 교체)
RUN pip install "paddlepaddle>=3.0,<4"

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

EXPOSE 3002

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
