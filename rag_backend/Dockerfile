FROM python:3.11-slim

# HF_HOME: HuggingFace 모델 캐시 위치 (docker-compose에서 볼륨으로 마운트하여 재다운로드 방지)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models/huggingface \
    PORT=3001

WORKDIR /app

# CPU 전용 torch를 먼저 설치 (CUDA 포함 기본 torch는 이미지가 수 GB 커짐)
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

EXPOSE 3001

# 모델 사전 로딩(app/main.py import 시점)이 끝나야 포트가 열림 — 최초 실행 시 모델 다운로드로 수 분 소요
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
