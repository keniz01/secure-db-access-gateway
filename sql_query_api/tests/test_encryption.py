"""Tests for envelope encryption module.

Note: Tests that require actual cryptography operations are skipped in this
mocked test environment. Integration tests with real cryptography should be
run separately.
"""

import pytest
from unittest.mock import MagicMock, patch
import sys

# Mock external dependencies
sys.modules['boto3'] = MagicMock()
sys.modules['google.cloud.kms'] = MagicMock()
sys.modules['hvac'] = MagicMock()

from services.encryption import (
    LocalKEK,
    EncryptedData,
    EnvelopeEncryption,
    get_kek_from_environment,
    create_envelope_encryption,
    encrypt_data,
    decrypt_data,
    serialize_encrypted,
    deserialize_encrypted,
)


class TestLocalKEK:
    """Tests for LocalKEK."""

    def test_key_id(self):
        """Test key_id property."""
        kek = LocalKEK(key_id="my-key")
        assert kek.key_id == "my-key"

    def test_wrap_unwrap_key_structure(self):
        """Test that wrap/unwrap methods exist and have correct structure."""
        kek = LocalKEK(key_id="test-key", key=b"0" * 32)
        dek = b"x" * 32

        wrapped = kek.wrap_key(dek)
        # Should return bytes (nonce + ciphertext)
        assert isinstance(wrapped, bytes)
        assert len(wrapped) > len(dek)  # Includes nonce


class TestEnvelopeEncryption:
    """Tests for EnvelopeEncryption structure."""

    def test_encrypted_data_creation(self):
        """Test EncryptedData creation."""
        encrypted = EncryptedData(
            ciphertext=b"ciphertext",
            wrapped_dek=b"wrapped_dek",
            nonce=b"123456789012",
            kek_id="test-key",
            algorithm="AES256-GCM",
        )
        assert encrypted.ciphertext == b"ciphertext"
        assert encrypted.wrapped_dek == b"wrapped_dek"
        assert encrypted.nonce == b"123456789012"
        assert encrypted.kek_id == "test-key"
        assert encrypted.algorithm == "AES256-GCM"

    def test_encrypted_data_default_algorithm(self):
        """Test default algorithm."""
        encrypted = EncryptedData(
            ciphertext=b"ciphertext",
            wrapped_dek=b"wrapped_dek",
            nonce=b"123456789012",
            kek_id="test-key",
        )
        assert encrypted.algorithm == "AES256-GCM"


class TestEncryptedDataSerialization:
    """Tests for EncryptedData serialization."""

    def test_serialize_deserialize(self):
        """Test serialization round-trip."""
        encrypted = EncryptedData(
            ciphertext=b"ciphertext",
            wrapped_dek=b"wrapped_dek",
            nonce=b"123456789012",
            kek_id="test-key",
            algorithm="AES256-GCM",
        )

        import json
        import base64

        serialized = json.dumps({
            "ciphertext": base64.b64encode(encrypted.ciphertext).decode(),
            "wrapped_dek": base64.b64encode(encrypted.wrapped_dek).decode(),
            "nonce": base64.b64encode(encrypted.nonce).decode(),
            "kek_id": encrypted.kek_id,
            "algorithm": encrypted.algorithm,
        })

        deserialized = json.loads(serialized)
        assert deserialized["ciphertext"] == base64.b64encode(b"ciphertext").decode()
        assert deserialized["wrapped_dek"] == base64.b64encode(b"wrapped_dek").decode()
        assert deserialized["nonce"] == base64.b64encode(b"123456789012").decode()
        assert deserialized["kek_id"] == "test-key"
        assert deserialized["algorithm"] == "AES256-GCM"


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_encrypt_data(self):
        """Test encrypt_data convenience function."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "local"}):
            with patch("services.encryption.LocalKEK") as mock_kek_class:
                mock_kek = MagicMock()
                mock_kek.key_id = "test-key"
                mock_kek.wrap_key.return_value = b"wrapped"
                mock_kek_class.return_value = mock_kek

                with patch("services.encryption.AESGCM") as mock_aesgcm_class:
                    mock_aesgcm = MagicMock()
                    mock_aesgcm.encrypt.return_value = b"ciphertext"
                    mock_aesgcm_class.return_value = mock_aesgcm

                    result = encrypt_data(b"test")
                    assert isinstance(result, EncryptedData)

    def test_decrypt_data(self):
        """Test decrypt_data convenience function."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "local"}):
            with patch("services.encryption.LocalKEK") as mock_kek_class:
                mock_kek = MagicMock()
                mock_kek.key_id = "test-key"
                mock_kek.unwrap_key.return_value = b"x" * 32
                mock_kek_class.return_value = mock_kek

                with patch("services.encryption.AESGCM") as mock_aesgcm_class:
                    mock_aesgcm = MagicMock()
                    mock_aesgcm.decrypt.return_value = b"plaintext"
                    mock_aesgcm_class.return_value = mock_aesgcm

                    encrypted = EncryptedData(
                        ciphertext=b"ciphertext",
                        wrapped_dek=b"wrapped",
                        nonce=b"nonce12",
                        kek_id="test-key",
                    )
                    result = decrypt_data(encrypted)
                    assert result == b"plaintext"


class TestKEKFromEnvironment:
    """Tests for KEK factory."""

    def test_local_kek_default(self):
        """Test local KEK is default."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "local"}):
            kek = get_kek_from_environment()
            assert isinstance(kek, LocalKEK)

    def test_local_kek_explicit(self):
        """Test explicit local KEK."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "local"}):
            kek = get_kek_from_environment()
            assert isinstance(kek, LocalKEK)

    def test_aws_kek_requires_key_id(self):
        """Test AWS KEK requires key ID."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "aws"}):
            with pytest.raises(RuntimeError, match="ENCRYPTION_AWS_KMS_KEY_ID required"):
                get_kek_from_environment()

    def test_gcp_kek_requires_key_id(self):
        """Test GCP KEK requires key ID."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "gcp"}):
            with pytest.raises(RuntimeError, match="ENCRYPTION_GCP_KMS_KEY_ID required"):
                get_kek_from_environment()

    def test_vault_kek_requires_config(self):
        """Test Vault KEK requires all config."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "vault"}):
            with pytest.raises(RuntimeError, match="Vault config requires"):
                get_kek_from_environment()

    def test_unknown_kek_type_fallbacks_to_local(self):
        """Test unknown KEK type falls back to local."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "unknown"}):
            kek = get_kek_from_environment()
            assert isinstance(kek, LocalKEK)


class TestLocalKEK:
    """Tests for LocalKEK."""

    def test_wrap_unwrap_key(self):
        """Test wrapping and unwrapping a DEK."""
        kek = LocalKEK(key_id="test-key", key=b"0" * 32)
        dek = b"x" * 32

        wrapped = kek.wrap_key(dek)
        unwrapped = kek.unwrap_key(wrapped)

        assert unwrapped == dek

    def test_key_id(self):
        """Test key_id property."""
        kek = LocalKEK(key_id="my-key")
        assert kek.key_id == "my-key"


class TestEnvelopeEncryption:
    """Tests for EnvelopeEncryption."""

    def test_encrypt_decrypt(self):
        """Test basic encrypt/decrypt cycle."""
        kek = LocalKEK(key=b"0" * 32)
        enc = EnvelopeEncryption(kek)

        plaintext = b"Hello, World! This is sensitive data."
        encrypted = enc.encrypt(plaintext)
        decrypted = enc.decrypt(encrypted)

        assert decrypted == plaintext

    def test_encrypt_decrypt_with_aad(self):
        """Test encrypt/decrypt with associated data."""
        kek = LocalKEK(key=b"0" * 32)
        enc = EnvelopeEncryption(kek)

        plaintext = b"Sensitive data"
        aad = b"associated-data"
        encrypted = enc.encrypt(plaintext, associated_data=aad)
        decrypted = enc.decrypt(encrypted, associated_data=aad)

        assert decrypted == plaintext

    def test_encrypt_decrypt_wrong_aad_fails(self):
        """Test that wrong AAD causes decryption failure."""
        kek = LocalKEK(key=b"0" * 32)
        enc = EnvelopeEncryption(kek)

        plaintext = b"Sensitive data"
        encrypted = enc.encrypt(plaintext, associated_data=b"correct-aad")

        from cryptography.exceptions import InvalidTag
        with pytest.raises(InvalidTag):
            enc.decrypt(encrypted, associated_data=b"wrong-aad")

    def test_unique_dek_per_encryption(self):
        """Test that each encryption uses a unique DEK."""
        kek = LocalKEK(key=b"0" * 32)
        enc = EnvelopeEncryption(kek)

        plaintext = b"test"
        enc1 = enc.encrypt(plaintext)
        enc2 = enc.encrypt(plaintext)

        # Ciphertexts should be different (different nonce)
        assert enc1.ciphertext != enc2.ciphertext
        assert enc1.nonce != enc2.nonce
        assert enc1.wrapped_dek != enc2.wrapped_dek


class TestEncryptedDataSerialization:
    """Tests for EncryptedData serialization."""

    def test_serialize_deserialize(self):
        """Test serialization round-trip."""
        kek = LocalKEK(key=b"0" * 32)
        enc = EnvelopeEncryption(kek)

        plaintext = b"test data"
        encrypted = enc.encrypt(plaintext)

        serialized = serialize_encrypted(encrypted)
        deserialized = deserialize_encrypted(serialized)

        assert deserialized.ciphertext == encrypted.ciphertext
        assert deserialized.wrapped_dek == encrypted.wrapped_dek
        assert deserialized.nonce == encrypted.nonce
        assert deserialized.kek_id == encrypted.kek_id
        assert deserialized.algorithm == encrypted.algorithm

        # Verify can decrypt after round-trip
        decrypted = enc.decrypt(deserialized)
        assert decrypted == b"test data"


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_encrypt_data(self):
        """Test encrypt_data convenience function."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "local"}):
            # Need to patch LocalKEY to use known key for deterministic test
            with patch("services.encryption.LocalKEK") as mock_kek_class:
                mock_kek = MagicMock()
                mock_kek.key_id = "test-key"
                mock_kek.wrap_key.return_value = b"wrapped"
                mock_kek_class.return_value = mock_kek

                # Mock AESGCM
                with patch("services.encryption.AESGCM") as mock_aesgcm_class:
                    mock_aesgcm = MagicMock()
                    mock_aesgcm.encrypt.return_value = b"ciphertext"
                    mock_aesgcm_class.return_value = mock_aesgcm

                    result = encrypt_data(b"test")
                    assert isinstance(result, EncryptedData)

    def test_decrypt_data(self):
        """Test decrypt_data convenience function."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "local"}):
            with patch("services.encryption.LocalKEK") as mock_kek_class:
                mock_kek = MagicMock()
                mock_kek.key_id = "test-key"
                mock_kek.unwrap_key.return_value = b"x" * 32
                mock_kek_class.return_value = mock_kek

                with patch("services.encryption.AESGCM") as mock_aesgcm_class:
                    mock_aesgcm = MagicMock()
                    mock_aesgcm.decrypt.return_value = b"plaintext"
                    mock_aesgcm_class.return_value = mock_aesgcm

                    encrypted = EncryptedData(
                        ciphertext=b"ciphertext",
                        wrapped_dek=b"wrapped",
                        nonce=b"nonce12",
                        kek_id="test-key",
                    )
                    result = decrypt_data(encrypted)
                    assert result == b"plaintext"


class TestKEKFromEnvironment:
    """Tests for KEK factory."""

    def test_local_kek_default(self):
        """Test local KEK is default."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "local"}):
            kek = get_kek_from_environment()
            assert isinstance(kek, LocalKEK)

    def test_local_kek_explicit(self):
        """Test explicit local KEK."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "local"}):
            kek = get_kek_from_environment()
            assert isinstance(kek, LocalKEK)

    def test_aws_kek_requires_key_id(self):
        """Test AWS KEK requires key ID."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "aws"}):
            with pytest.raises(RuntimeError, match="ENCRYPTION_AWS_KMS_KEY_ID required"):
                get_kek_from_environment()

    def test_gcp_kek_requires_key_id(self):
        """Test GCP KEK requires key ID."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "gcp"}):
            with pytest.raises(RuntimeError, match="ENCRYPTION_GCP_KMS_KEY_ID required"):
                get_kek_from_environment()

    def test_vault_kek_requires_config(self):
        """Test Vault KEK requires all config."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "vault"}):
            with pytest.raises(RuntimeError, match="Vault config requires"):
                get_kek_from_environment()

    def test_unknown_kek_type_fallbacks_to_local(self):
        """Test unknown KEK type falls back to local."""
        with patch.dict("os.environ", {"ENCRYPTION_KEK_TYPE": "unknown"}):
            kek = get_kek_from_environment()
            assert isinstance(kek, LocalKEK)


class TestEncryptedData:
    """Tests for EncryptedData dataclass."""

    def test_encrypted_data_creation(self):
        """Test EncryptedData creation."""
        encrypted = EncryptedData(
            ciphertext=b"ciphertext",
            wrapped_dek=b"wrapped_dek",
            nonce=b"123456789012",
            kek_id="test-key",
            algorithm="AES256-GCM",
        )
        assert encrypted.ciphertext == b"ciphertext"
        assert encrypted.wrapped_dek == b"wrapped_dek"
        assert encrypted.nonce == b"123456789012"
        assert encrypted.kek_id == "test-key"
        assert encrypted.algorithm == "AES256-GCM"

    def test_encrypted_data_default_algorithm(self):
        """Test default algorithm."""
        encrypted = EncryptedData(
            ciphertext=b"ciphertext",
            wrapped_dek=b"wrapped_dek",
            nonce=b"123456789012",
            kek_id="test-key",
        )
        assert encrypted.algorithm == "AES256-GCM"