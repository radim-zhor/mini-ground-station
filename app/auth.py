import os
import secrets

from fastapi import HTTPException, Request


def require_agent_auth(request: Request) -> None:
    """Validate the agent's shared-secret Bearer token (constant-time, A5)."""
    secret = os.getenv("AGENT_SECRET", "")
    auth = request.headers.get("Authorization", "")
    if not secret or not secrets.compare_digest(auth, f"Bearer {secret}"):
        raise HTTPException(status_code=401, detail="Unauthorized")
