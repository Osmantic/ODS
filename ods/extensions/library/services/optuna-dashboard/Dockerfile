FROM ghcr.io/optuna/optuna-dashboard:v0.21.0@sha256:b64bd33154185814d76aaab52ac175f2d409af79ba5b476a6df1af3b97a3b254
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin optuna \
    && mkdir -p /data /app/ods_examples && chown -R 1000:1000 /data /app
COPY --chown=1000:1000 study.py /app/ods_examples/study.py
USER 1000:1000
