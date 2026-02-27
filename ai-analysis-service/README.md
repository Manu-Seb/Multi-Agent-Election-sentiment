# AI Analysis Service

Entity extraction, sentiment scoring, co-occurrence detection, and entity-graph schema management.

## Structure

```
ai-analysis-service/
├── src/
│   ├── __init__.py
│   ├── entity_graph/
│   │   ├── __init__.py
│   │   ├── avro/
│   │   │   ├── raw-articles-value.avsc
│   │   │   ├── entity-sentiment-value.avsc
│   │   │   ├── graph-changes-value.avsc
│   │   │   └── graph-snapshots-value.avsc
│   │   ├── models.py
│   │   ├── wire_models.py
│   │   └── schema_registry.py
│   ├── processors/
│   │   ├── __init__.py
│   │   ├── entity_extractor.py
│   │   ├── sentiment_analyzer.py
│   │   └── co_occurrence.py
│   └── kafka/
│       ├── __init__.py
│       ├── consumer.py
│       └── producer.py
├── tests/
├── deploy/
│   ├── __init__.py
│   └── register_schemas.py
├── Dockerfile
├── requirements.txt
├── setup.py
└── README.md
```

## Install (editable)

```bash
pip install -e .
```
