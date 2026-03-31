FROM python:3.11-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && \
    apt-get install --no-install-recommends -y ffmpeg gcc libffi-dev libjpeg62-turbo-dev libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip setuptools && pip install -r requirements.txt

COPY . .

CMD ["python", "-m", "shivu"]
