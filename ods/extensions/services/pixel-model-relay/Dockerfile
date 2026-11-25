FROM python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e

WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY relay.py ./
# Docker COPY retains a strict host umask; the non-root relay must read its code.
RUN chmod -R a+rX /app

RUN useradd -m -u 1000 pixel
USER pixel
EXPOSE 4102
CMD ["python3", "relay.py"]
