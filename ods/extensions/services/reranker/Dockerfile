FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG DEFAULT_MODEL=BAAI/bge-reranker-base
ENV RERANKER_MODEL=BAAI/bge-reranker-base
RUN python -c "from sentence_transformers import CrossEncoder; import os; CrossEncoder(os.environ['RERANKER_MODEL'])"

COPY main.py .

EXPOSE 3010

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3010"]
