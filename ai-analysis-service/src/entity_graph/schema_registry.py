from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import httpx


SCHEMA_SUBJECTS = {
    "raw-articles-value": "raw-articles-value",
    "entity-sentiment-value": "entity-sentiment-value",
    "graph-changes-value": "graph-changes-value",
    "graph-snapshots-value": "graph-snapshots-value",
}


@dataclass(frozen=True)
class RegisteredSchema:
    subject: str
    schema_id: int
    version: int | None = None


@dataclass(frozen=True)
class SchemaVersionInfo:
    subject: str
    version: int
    schema_id: int


class SchemaRegistryClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        auth: tuple[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.auth = auth

    def _get(self, path: str) -> dict | list:
        response = httpx.get(
            f"{self.base_url}{path}",
            timeout=self.timeout_seconds,
            auth=self.auth,
        )
        response.raise_for_status()
        return response.json()

    def _put(self, path: str, payload: dict) -> dict:
        response = httpx.put(
            f"{self.base_url}{path}",
            json=payload,
            timeout=self.timeout_seconds,
            auth=self.auth,
        )
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, payload: dict) -> dict:
        response = httpx.post(
            f"{self.base_url}{path}",
            json=payload,
            timeout=self.timeout_seconds,
            auth=self.auth,
        )
        response.raise_for_status()
        return response.json()

    def set_subject_compatibility(self, subject: str, level: str = "BACKWARD") -> dict:
        return self._put(f"/config/{subject}", {"compatibility": level})

    def get_subject_compatibility(self, subject: str) -> str:
        data = self._get(f"/config/{subject}")
        return data["compatibilityLevel"]

    def check_compatibility(self, subject: str, schema: str, version: str = "latest") -> bool:
        payload = {"schema": schema, "schemaType": "AVRO"}
        data = self._post(f"/compatibility/subjects/{subject}/versions/{version}", payload)
        return bool(data["is_compatible"])

    def register_schema(self, subject: str, schema: str) -> RegisteredSchema:
        payload = {"schema": schema, "schemaType": "AVRO"}
        data = self._post(f"/subjects/{subject}/versions", payload)
        schema_id = data["id"]

        latest = self.get_latest_schema_version(subject)
        return RegisteredSchema(subject=subject, schema_id=schema_id, version=latest.version)

    def get_versions(self, subject: str) -> list[int]:
        data = self._get(f"/subjects/{subject}/versions")
        return [int(item) for item in data]

    def get_latest_schema_version(self, subject: str) -> SchemaVersionInfo:
        data = self._get(f"/subjects/{subject}/versions/latest")
        return SchemaVersionInfo(subject=subject, version=data["version"], schema_id=data["id"])


class SchemaRegistryManager:
    def __init__(self, client: SchemaRegistryClient, schema_dir: str | Path) -> None:
        self.client = client
        self.schema_dir = Path(schema_dir)

    def load_schema(self, schema_file: str) -> str:
        path = self.schema_dir / schema_file
        return json.dumps(json.loads(path.read_text()))

    def register_all(
        self,
        subject_to_file: dict[str, str],
        *,
        compatibility: str = "BACKWARD",
        check_only: bool = False,
    ) -> dict[str, SchemaVersionInfo | bool]:
        results: dict[str, SchemaVersionInfo | bool] = {}

        for subject, schema_file in subject_to_file.items():
            schema_text = self.load_schema(schema_file)
            self.client.set_subject_compatibility(subject, compatibility)

            is_compatible = self.client.check_compatibility(subject, schema_text)
            if not is_compatible:
                raise ValueError(f"Schema is not {compatibility}-compatible for subject '{subject}'")

            if check_only:
                results[subject] = True
                continue

            self.client.register_schema(subject, schema_text)
            results[subject] = self.client.get_latest_schema_version(subject)

        return results


DEFAULT_SUBJECT_TO_FILE = {
    "raw-articles-value": "raw-articles-value.avsc",
    "entity-sentiment-value": "entity-sentiment-value.avsc",
    "graph-changes-value": "graph-changes-value.avsc",
    "graph-snapshots-value": "graph-snapshots-value.avsc",
}
