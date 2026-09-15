FROM ghcr.io/mlflow/mlflow:v3.16.0@sha256:470607da2a70fa38724d3d7db0a54a78b50fac708f7291bfebe1935fa95259ae
# The official image omits the two additional mlflow[auth] dependencies.
COPY requirements-auth.txt /tmp/requirements-auth.txt
RUN python -m pip install --no-cache-dir --no-deps --require-hashes -r /tmp/requirements-auth.txt && python -m pip check
COPY entrypoint.py /opt/ods-mlflow-entrypoint.py
USER 1000:1000
WORKDIR /data
ENTRYPOINT ["python", "/opt/ods-mlflow-entrypoint.py"]
