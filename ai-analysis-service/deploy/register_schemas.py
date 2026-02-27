from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from entity_graph.schema_registry import (  # noqa: E402
    DEFAULT_SUBJECT_TO_FILE,
    SchemaRegistryClient,
    SchemaRegistryManager,
)


def main() -> None:
    registry_url = os.getenv("SCHEMA_REGISTRY_URL", "http://localhost:8081")
    compatibility = os.getenv("SCHEMA_COMPATIBILITY", "BACKWARD")
    check_only = os.getenv("SCHEMA_CHECK_ONLY", "false").lower() == "true"

    client = SchemaRegistryClient(base_url=registry_url)
    manager = SchemaRegistryManager(client=client, schema_dir=SRC / "entity_graph" / "avro")

    result = manager.register_all(
        subject_to_file=DEFAULT_SUBJECT_TO_FILE,
        compatibility=compatibility,
        check_only=check_only,
    )

    for subject, value in result.items():
        print(f"{subject}: {value}")


if __name__ == "__main__":
    main()
