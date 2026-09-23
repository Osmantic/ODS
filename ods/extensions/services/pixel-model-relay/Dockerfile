FROM python:3.11-slim@sha256:da047cb8f9d1d98e5c070f5300ba9f7274e33b8fc0e5be5ed88740aed1b95ba9

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip==26.2.1 setuptools==84.0.0 \
    && pip install --no-cache-dir -r requirements.txt \
    && pip uninstall --yes pip setuptools wheel
COPY relay.py ./

RUN useradd -m -u 1000 pixel
USER pixel
EXPOSE 4102
CMD ["python3", "relay.py"]
