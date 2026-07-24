FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY server.py /app/server.py

ENV HOME=/app \
    MODELSCOPE_CACHE=/app/cache
RUN adduser --system --no-create-home --uid 1000 sensevoice \
    && mkdir -p /app/cache \
    && chown -R sensevoice:nogroup /app
USER sensevoice

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
