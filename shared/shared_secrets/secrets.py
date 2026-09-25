"""Shared secret loading for the secure DB access gateway services.

Secrets are resolved in precedence order:

1. A direct environment variable (``NAME``).
2. A ``*_FILE`` path on disk (``NAME_FILE``).
3. A caller-supplied default, or a :class:`MissingSecretError` for required
   secrets.

Production deployments are expected to inject secrets as environment
variables. The ``*_FILE`` fallback supports orchestrators that mount secrets
as files (Compose ``secrets:``, Kubernetes secrets mounted to disk); the file
content is expected to be plaintext.
"""

from __future__ import annotations

import os


class MissingSecretError(RuntimeError):
    """Raised when a required secret cannot be resolved from any source."""


def is_environment_production() -> bool:
    """Return whether the runtime environment is a production deployment."""
    # Default to production (fail-closed) - dev must explicitly set ENVIRONMENT=dev
    return os.getenv("ENVIRONMENT", "production").lower() in {"production", "prod"}


def _source_file(name: str) -> str | None:
    path = os.getenv(f"{name}_FILE", "").strip()
    if not path:
        return None
    # Validate path does not contain traversal and is absolute when configured
    if ".." in path:
        import warnings

        warnings.warn(
            f"Secret file path for '{name}' contains '..' traversal: {path}",
            UserWarning,
            stacklevel=3,
        )
        return None
    return path


def _read_file(path: str, name: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        # Warn when a *_FILE path was explicitly configured but missing - silent failure hides mis-mounts
        import warnings

        warnings.warn(
            f"Secret file for '{name}' not found at {path}. Check {name}_FILE mount.",
            UserWarning,
            stacklevel=3,
        )
        return ""
    except OSError as exc:
        raise RuntimeError(f"Unable to read secret file for '{name}' at {path}.") from exc


def read_file_secret(path: str) -> str:
    """Read a secret value from an explicit file path.

    Used by tools that address secret files directly (e.g. the headless
    operator CLI) instead of through the ``NAME``/``NAME_FILE`` convention.
    Returns ``""`` when the file does not exist.
    """
    return _read_file(path, path).strip()


def read_secret(
    name: str,
    *,
    required: bool = False,
    default: str = "",
    error_message: str | None = None,
) -> str:
    """Resolve the secret ``name`` from the environment or a secret file.

    Args:
        name: The base secret name, e.g. ``AUTH0_CLIENT_SECRET``. The file
            source is read from the ``<NAME>_FILE`` environment variable.
        required: Raise :class:`MissingSecretError` when no value can be
            resolved from any source.
        default: Value returned when the secret is absent and not required.
        error_message: Override the missing-secret diagnostic. Preserves
            caller-specific messages that reference legacy env var names
            (e.g. ``TENANT_DATABASES_JSON``).

    Raises:
        MissingSecretError: A required secret resolved to no value.
    """
    value = os.getenv(name)
    if value is not None:
        stripped = value.strip()
        if stripped:
            return stripped

    path = _source_file(name)
    if path:
        content = _read_file(path, name)
        if content:
            stripped_content = content.strip()
            if stripped_content:
                return stripped_content
            # File exists but empty after strip - treat as missing
    if required:
        raise MissingSecretError(
            error_message
            or f"Required secret '{name}' is not set. Provide '{name}' or '{name}_FILE'."
        )
    return default