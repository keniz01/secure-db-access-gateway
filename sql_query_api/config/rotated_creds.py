"""Load rotated PostgreSQL credentials from shared volume."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Optional


@dataclass(frozen=True, slots=True)
class RotatedCredentials:
    tenant_id: str
    role_name: str
    database: str
    username: str
    password: str
    rotated_at: str
    host: str
    port: int


class RotatedCredentialLoader:
    """Loads and caches rotated credentials from filesystem."""

    def __init__(self, creds_dir: str = "/creds") -> None:
        self._creds_dir = Path(creds_dir)
        self._cache: dict[str, RotatedCredentials] = {}
        self._lock = Lock()

    def _load_file(self, tenant_id: str, role_name: str) -> Optional[RotatedCredentials]:
        """Load credentials from JSON file."""
        file_path = self._creds_dir / f"{tenant_id}_{role_name}.json"
        if not file_path.exists():
            return None

        try:
            with file_path.open() as f:
                data = json.load(f)
            return RotatedCredentials(
                tenant_id=data["tenant_id"],
                role_name=data["role_name"],
                database=data["database"],
                username=data["username"],
                password=data["password"],
                rotated_at=data["rotated_at"],
                host=data["host"],
                port=data["port"],
            )
        except (json.JSONDecodeError, KeyError, OSError) as e:
            # Log but don't crash - fallback to stale cache
            print(f"WARNING: Failed to load credentials for {tenant_id}_{role_name}: {e}")
            return None

    def get_credentials(self, tenant_id: str, role_name: str) -> RotatedCredentials:
        """Get credentials, reloading from disk if not cached."""
        cache_key = f"{tenant_id}_{role_name}"

        with self._lock:
            # Check cache first
            if cache_key in self._cache:
                return self._cache[cache_key]

            # Load from file
            creds = self._load_file(tenant_id, role_name)
            if creds is None:
                raise FileNotFoundError(
                    f"No rotated credentials found for {tenant_id}/{role_name} "
                    f"in {self._creds_dir}"
                )

            self._cache[cache_key] = creds
            return creds

    def invalidate_cache(self, tenant_id: str, role_name: str) -> None:
        """Force reload on next get_credentials call."""
        cache_key = f"{tenant_id}_{role_name}"
        with self._lock:
            self._cache.pop(cache_key, None)

    def list_available(self) -> list[str]:
        """List available credential files."""
        return [f.stem for f in self._creds_dir.glob("*.json")]


def get_credential_loader() -> RotatedCredentialLoader:
    """Factory for credential loader from environment."""
    creds_dir = os.getenv("CREDS_DIR", "/creds")
    return RotatedCredentialLoader(creds_dir)