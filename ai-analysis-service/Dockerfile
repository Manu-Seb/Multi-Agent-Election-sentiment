FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && python -m spacy download en_core_web_sm

COPY src /app/src

ENV PYTHONPATH=/app/src
ENV KAFKA_GROUP_ID=entity-sentiment-processor

ENTRYPOINT ["python", "/app/src/main.py"]
