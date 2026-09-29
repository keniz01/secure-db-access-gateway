"""Shared mTLS HTTP client for service-to-service communication."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import httpx

from shared_secrets import read_secret


@dataclass(frozen=True, slots=True)
class MTLSConfig:
    """mTLS configuration for service-to-service communication."""

    enabled: bool = False
    cert_path: Optional[str] = None
    key_path: Optional[str] = None
    ca_path: Optional[str] = None
    verify: bool = True

    @classmethod
    def from_environment(cls, prefix: str = "SQL_QUERY_API") -> "MTLSConfig":
        """Load mTLS configuration from environment variables.

        Environment variables (with prefix):
        - {prefix}_MTLS_ENABLED
        - {prefix}_MTLS_CERT_PATH
        - {prefix}_MTLS_KEY_PATH
        - {prefix}_MTLS_CA_PATH
        - {prefix}_MTLS_VERIFY
        """
        enabled = os.getenv(f"{prefix}_MTLS_ENABLED", "").strip().lower() in ("true", "1", "yes")
        cert_path = os.getenv(f"{prefix}_MTLS_CERT_PATH") or read_secret(f"{prefix}_MTLS_CERT_PATH", required=False)
        key_path = os.getenv(f"{prefix}_MTLS_KEY_PATH") or read_secret(f"{prefix}_MTLS_KEY_PATH", required=False)
        ca_path = os.getenv(f"{prefix}_MTLS_CA_PATH") or read_secret(f"{prefix}_MTLS_CA_PATH", required=False)
        verify = os.getenv(f"{prefix}_MTLS_VERIFY", "").strip().lower() not in ("false", "0", "no")

        return cls(
            enabled=enabled,
            cert_path=cert_path,
            key_path=key_path,
            ca_path=ca_path,
            verify=verify,
        )


def create_mtls_client(
    base_url: str,
    timeout: float = 30.0,
    mtls_config: Optional[MTLSConfig] = None,
    default_headers: Optional[dict[str, str]] = None,
) -> httpx.AsyncClient:
    """Create an httpx.AsyncClient with optional mTLS configuration.

    Args:
        base_url: Base URL for the client
        timeout: Request timeout in seconds
        mtls_config: mTLS configuration (loads from env if not provided)
        default_headers: Default headers to include in all requests

    Returns:
        Configured httpx.AsyncClient
    """
    if mtls_config is None:
        mtls_config = MTLSConfig.from_environment()

    client_kwargs = {
        "base_url": base_url,
        "timeout": timeout,
        "headers": {"Content-Type": "application/json"},
    }

    if default_headers:
        client_kwargs["headers"].update(default_headers)

    if mtls_config and mtls_config.enabled:
        if not mtls_config.cert_path or not mtls_config.key_path:
            raise RuntimeError("mTLS enabled but cert/key paths not configured")
        client_kwargs["cert"] = (mtls_config.cert_path, mtls_config.key_path)
        if mtls_config.ca_path:
            client_kwargs["verify"] = mtls_config.ca_path
        else:
            client_kwargs["verify"] = mtls_config.verify

    return httpx.AsyncClient(**client_kwargs)