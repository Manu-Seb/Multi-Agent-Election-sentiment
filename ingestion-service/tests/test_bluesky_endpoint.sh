#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INGESTION_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$INGESTION_DIR/.." && pwd)"
ENV_FILE="$INGESTION_DIR/.env"
PYTHON_BIN="$INGESTION_DIR/venv/bin/python"

QUERY="${1:-}"
LIMIT="${2:-}"
RAW_JSON="${RAW_JSON:-false}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing python executable: $PYTHON_BIN"
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

QUERY="${QUERY:-${BLUESKY_SEARCH_TERMS%%,*}}"
QUERY="${QUERY:-election}"
LIMIT="${LIMIT:-${BLUESKY_LIMIT:-5}}"

if [[ -z "${BLUESKY_USERNAME:-}" ]]; then
  echo "Missing BLUESKY_USERNAME in $ENV_FILE"
  exit 1
fi

if [[ -z "${BLUESKY_PASSWORD:-}" ]]; then
  echo "Missing BLUESKY_PASSWORD in $ENV_FILE"
  exit 1
fi

export BLUESKY_USERNAME
export BLUESKY_PASSWORD
export TEST_BLUESKY_QUERY="$QUERY"
export TEST_BLUESKY_LIMIT="$LIMIT"
export TEST_BLUESKY_RAW_JSON="$RAW_JSON"

echo "== Bluesky Smoke Test =="
echo "env file: $ENV_FILE"
echo "python: $PYTHON_BIN"
echo "query: $QUERY"
echo "limit: $LIMIT"
echo

"$PYTHON_BIN" - <<'PY'
import json
import os
import sys

try:
    from bluesky_search import BlueskyPostsFetcher
except ImportError:
    try:
        from src.bluesky_search import BlueskyPostsFetcher
    except ImportError as exc:
        raise SystemExit("Could not import bluesky-search in ingestion-service venv.") from exc


def validate_post(post, index):
    errors = []
    if not isinstance(post, dict):
        return [f"result {index}: expected dict, got {type(post).__name__}"]
    if not str(post.get("text", "")).strip():
        errors.append(f"result {index}: missing text")
    if not str(post.get("created_at") or post.get("indexed_at") or "").strip():
        errors.append(f"result {index}: missing created_at/indexed_at")
    author = post.get("author") or {}
    if not isinstance(author, dict) or not str(author.get("handle", "")).strip():
        errors.append(f"result {index}: missing author.handle")
    return errors


def summarize_post(post, index):
    author = post.get("author") or {}
    text = " ".join(str(post.get("text", "")).split())
    if len(text) > 180:
        text = f"{text[:177]}..."
    print(f"[{index}] author: {author.get('display_name') or author.get('handle') or '<unknown>'}")
    print(f"    handle: {author.get('handle', '<missing>')}")
    print(f"    created_at: {post.get('created_at') or post.get('indexed_at') or '<missing>'}")
    print(f"    web_url: {post.get('web_url') or '<missing>'}")
    print(f"    text: {text or '<empty>'}")


query = os.environ["TEST_BLUESKY_QUERY"]
limit = int(os.environ["TEST_BLUESKY_LIMIT"])
raw_json = os.environ.get("TEST_BLUESKY_RAW_JSON", "false").lower() == "true"
username = os.environ["BLUESKY_USERNAME"]
password = os.environ["BLUESKY_PASSWORD"]

fetcher = BlueskyPostsFetcher(username=username, password=password)
posts = fetcher.search_posts(query, limit=limit) or []

print(f"returned_posts: {len(posts)}")
if not posts:
    print("No posts returned. Check the query, credentials, or network access.")
    sys.exit(1)

errors = []
for index, post in enumerate(posts, start=1):
    errors.extend(validate_post(post, index))
    if raw_json:
        print(f"[{index}]")
        print(json.dumps(post, indent=2, ensure_ascii=False))
    else:
        summarize_post(post, index)
    print()

if errors:
    print("validation_errors:")
    for error in errors:
        print(f"- {error}")
    sys.exit(2)

print("status: ok")
PY
