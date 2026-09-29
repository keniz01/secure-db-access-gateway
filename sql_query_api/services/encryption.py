"""Envelope encryption for data at rest.

Provides encryption/decryption using envelope encryption:
- Data Encryption Key (DEK) encrypts the data
- Key Encryption Key (KEK) from KMS wraps the DEK
- Supports AWS KMS, GCP KMS, HashiCorp Vault Transit, and local development
"""

from __future__ import annotations

import base64
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True, slots=True)
class EncryptedData:
    """Encrypted data with wrapped DEK."""
    ciphertext: bytes
    wrapped_dek: bytes
    nonce: bytes
    kek_id: str  # Identifier for the KEK used (e.g., KMS key ARN, Vault key name)
    algorithm: str = "AES256-GCM"


class KeyEncryptionKey(ABC):
    """Abstract base class for Key Encryption Keys."""

    @property
    @abstractmethod
    def key_id(self) -> str:
        """Return the identifier for this KEK."""
        ...

    @abstractmethod
    def wrap_key(self, dek: bytes) -> bytes:
        """Wrap (encrypt) a Data Encryption Key."""
        ...

    @abstractmethod
    def unwrap_key(self, wrapped_dek: bytes) -> bytes:
        """Unwrap (decrypt) a Data Encryption Key."""
        ...


class LocalKEK(KeyEncryptionKey):
    """Local KEK for development/testing only.

    WARNING: Not suitable for production! Key is stored in memory.
    """

    def __init__(self, key_id: str = "local-dev-kek", key: Optional[bytes] = None) -> None:
        self._key_id = key_id
        # In production, this would be loaded from a secure source
        # For dev, generate or use provided key
        self._master_key = key or os.urandom(32)

    @property
    def key_id(self) -> str:
        return self._key_id

    def wrap_key(self, dek: bytes) -> bytes:
        """Wrap DEK using AES-GCM with master key."""
        aesgcm = AESGCM(self._master_key)
        nonce = os.urandom(12)
        wrapped = aesgcm.encrypt(nonce, dek, None)
        return nonce + wrapped  # Prepend nonce for unwrapping

    def unwrap_key(self, wrapped_dek: bytes) -> bytes:
        """Unwrap DEK using AES-GCM with master key."""
        aesgcm = AESGCM(self._master_key)
        nonce = wrapped_dek[:12]
        ciphertext = wrapped_dek[12:]
        return aesgcm.decrypt(nonce, ciphertext, None)


class AWSKMSKEK(KeyEncryptionKey):
    """AWS KMS Key Encryption Key."""

    def __init__(self, key_id: str, region: Optional[str] = None) -> None:
        self._key_id = key_id
        self._region = region
        self._client = None

    @property
    def key_id(self) -> str:
        return self._key_id

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("kms", region_name=self._region)
        return self._client

    def wrap_key(self, dek: bytes) -> bytes:
        client = self._get_client()
        response = client.encrypt(KeyId=self._key_id, Plaintext=dek)
        return response["CiphertextBlob"]

    def unwrap_key(self, wrapped_dek: bytes) -> bytes:
        client = self._get_client()
        response = client.decrypt(CiphertextBlob=wrapped_dek)
        return response["Plaintext"]


class GCPKMSKEK(KeyEncryptionKey):
    """Google Cloud KMS Key Encryption Key."""

    def __init__(self, key_id: str) -> None:
        # key_id format: projects/PROJECT/locations/LOCATION/keyRings/RING/cryptoKeys/KEY
        self._key_id = key_id
        self._client = None

    @property
    def key_id(self) -> str:
        return self._key_id

    def _get_client(self):
        if self._client is None:
            from google.cloud import kms
            self._client = kms.KeyManagementServiceClient()
        return self._client

    def wrap_key(self, dek: bytes) -> bytes:
        client = self._get_client()
        response = client.encrypt(request={"name": self._key_id, "plaintext": dek})
        return response.ciphertext

    def unwrap_key(self, wrapped_dek: bytes) -> bytes:
        client = self._get_client()
        response = client.decrypt(request={"name": self._key_id, "ciphertext": wrapped_dek})
        return response.plaintext


class VaultTransitKEK(KeyEncryptionKey):
    """HashiCorp Vault Transit Key Encryption Key."""

    def __init__(self, key_name: str, vault_addr: str, token: str) -> None:
        self._key_name = key_name
        self._vault_addr = vault_addr.rstrip("/")
        self._token = token
        self._client = None

    @property
    def key_id(self) -> str:
        return f"vault:{self._key_name}"

    def _get_client(self):
        if self._client is None:
            import hvac
            self._client = hvac.Client(url=self._vault_addr, token=self._token)
        return self._client

    def wrap_key(self, dek: bytes) -> bytes:
        client = self._get_client()
        response = client.secrets.transit.encrypt_data(
            name=self._key_name,
            plaintext=base64.b64encode(dek).decode(),
        )
        return response["data"]["ciphertext"].encode()

    def unwrap_key(self, wrapped_dek: bytes) -> bytes:
        client = self._get_client()
        response = client.secrets.transit.decrypt_data(
            name=self._key_name,
            ciphertext=wrapped_dek.decode(),
        )
        return base64.b64decode(response["data"]["plaintext"])


class EnvelopeEncryption:
    """Envelope encryption using a KEK to wrap DEKs."""

    def __init__(self, kek: KeyEncryptionKey) -> None:
        self._kek = kek

    def encrypt(self, plaintext: bytes, associated_data: Optional[bytes] = None) -> EncryptedData:
        """Encrypt data using envelope encryption.

        Args:
            plaintext: Data to encrypt
            associated_data: Optional AAD for AES-GCM

        Returns:
            EncryptedData with ciphertext, wrapped DEK, and metadata
        """
        # Generate a fresh DEK for each encryption
        dek = os.urandom(32)
        aesgcm = AESGCM(dek)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data)

        # Wrap the DEK with the KEK
        wrapped_dek = self._kek.wrap_key(dek)

        return EncryptedData(
            ciphertext=ciphertext,
            wrapped_dek=wrapped_dek,
            nonce=nonce,
            kek_id=self._kek.key_id,
            algorithm="AES256-GCM",
        )

    def decrypt(self, encrypted: EncryptedData, associated_data: Optional[bytes] = None) -> bytes:
        """Decrypt data using envelope encryption.

        Args:
            encrypted: EncryptedData to decrypt
            associated_data: Optional AAD for AES-GCM

        Returns:
            Decrypted plaintext
        """
        # Unwrap the DEK
        dek = self._kek.unwrap_key(encrypted.wrapped_dek)

        # Decrypt the data
        aesgcm = AESGCM(dek)
        return aesgcm.decrypt(encrypted.nonce, encrypted.ciphertext, associated_data)


def get_kek_from_environment() -> KeyEncryptionKey:
    """Create a KEK from environment configuration.

    Environment variables:
    - ENCRYPTION_KEK_TYPE: local | aws | gcp | vault
    - ENCRYPTION_AWS_KMS_KEY_ID / ENCRYPTION_AWS_KMS_REGION
    - ENCRYPTION_GCP_KMS_KEY_ID
    - ENCRYPTION_VAULT_KEY_NAME / ENCRYPTION_VAULT_ADDR / ENCRYPTION_VAULT_TOKEN

    For local development, uses LocalKEK with ephemeral key.
    """
    kek_type = os.getenv("ENCRYPTION_KEK_TYPE", "local").lower()

    if kek_type == "aws":
        key_id = os.getenv("ENCRYPTION_AWS_KMS_KEY_ID")
        region = os.getenv("ENCRYPTION_AWS_KMS_REGION")
        if not key_id:
            raise RuntimeError("ENCRYPTION_AWS_KMS_KEY_ID required for AWS KMS")
        return AWSKMSKEK(key_id, region)

    elif kek_type == "gcp":
        key_id = os.getenv("ENCRYPTION_GCP_KMS_KEY_ID")
        if not key_id:
            raise RuntimeError("ENCRYPTION_GCP_KMS_KEY_ID required for GCP KMS")
        return GCPKMSKEK(key_id)

    elif kek_type == "vault":
        key_name = os.getenv("ENCRYPTION_VAULT_KEY_NAME")
        vault_addr = os.getenv("ENCRYPTION_VAULT_ADDR")
        token = os.getenv("ENCRYPTION_VAULT_TOKEN")
        if not all([key_name, vault_addr, token]):
            raise RuntimeError("Vault config requires key_name, addr, and token")
        return VaultTransitKEK(key_name, vault_addr, token)

    else:
        # Local development - uses ephemeral key
        return LocalKEK()


def create_envelope_encryption() -> EnvelopeEncryption:
    """Create EnvelopeEncryption from environment configuration."""
    kek = get_kek_from_environment()
    return EnvelopeEncryption(kek)


# Convenience functions for common operations
def encrypt_data(plaintext: bytes, associated_data: Optional[bytes] = None) -> EncryptedData:
    """Encrypt data using envelope encryption from environment config."""
    return create_envelope_encryption().encrypt(plaintext, associated_data)


def decrypt_data(encrypted: EncryptedData, associated_data: Optional[bytes] = None) -> bytes:
    """Decrypt data using envelope encryption from environment config."""
    return create_envelope_encryption().decrypt(encrypted, associated_data)


def serialize_encrypted(encrypted: EncryptedData) -> str:
    """Serialize EncryptedData to base64 JSON string for storage."""
    import json
    return json.dumps({
        "ciphertext": base64.b64encode(encrypted.ciphertext).decode(),
        "wrapped_dek": base64.b64encode(encrypted.wrapped_dek).decode(),
        "nonce": base64.b64encode(encrypted.nonce).decode(),
        "kek_id": encrypted.kek_id,
        "algorithm": encrypted.algorithm,
    })


def deserialize_encrypted(data: str) -> EncryptedData:
    """Deserialize EncryptedData from base64 JSON string."""
    import json
    obj = json.loads(data)
    return EncryptedData(
        ciphertext=base64.b64decode(obj["ciphertext"]),
        wrapped_dek=base64.b64decode(obj["wrapped_dek"]),
        nonce=base64.b64decode(obj["nonce"]),
        kek_id=obj["kek_id"],
        algorithm=obj.get("algorithm", "AES256-GCM"),
    )