FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY relay.py ./

RUN useradd -m -u 1000 pixel
USER pixel
EXPOSE 4102
CMD ["python3", "relay.py"]
