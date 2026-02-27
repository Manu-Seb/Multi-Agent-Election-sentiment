from __future__ import annotations

from fastapi import HTTPException


def validate_jwt_placeholder(token: str | None, required: bool) -> dict:
    """JWT validation placeholder.

    Replace with real JWT verification against your identity provider.
    """
    if not required:
        return {"sub": "anonymous", "scopes": ["read"]}
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if token == "test-token":
        return {"sub": "test-user", "scopes": ["read"]}
    raise HTTPException(status_code=401, detail="Invalid token")
