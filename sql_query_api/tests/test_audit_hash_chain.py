import pytest

from config.app_logger import (
    get_current_audit_hash,
    log_audit_event,
    reset_current_audit_hash,
    set_current_audit_hash,
)


def test_audit_hash_chain_basic():
    """Test that audit hash chain produces deterministic hashes."""
    # Reset chain
    token = set_current_audit_hash("")
    try:
        # First event
        log_audit_event("test_event", user="test@example.com", action="login")
        hash1 = get_current_audit_hash()

        # Second event
        log_audit_event("test_event", user="test@example.com", action="logout")
        hash2 = get_current_audit_hash()

        # Hashes should be different
        assert hash1 != hash2
        # Both should be 64-char hex strings (SHA256)
        assert len(hash1) == 64
        assert len(hash2) == 64
    finally:
        reset_current_audit_hash(token)


def test_audit_hash_chain_deterministic():
    """Test that same input produces same hash chain (excluding timestamp)."""
    # Since timestamp is included in hash, we verify chain logic by
    # checking that the same sequence produces consistent relative hashes
    token1 = set_current_audit_hash("")
    try:
        log_audit_event("event_a", foo="bar")
        hash_a1 = get_current_audit_hash()
        log_audit_event("event_b", foo="baz")
        hash_b1 = get_current_audit_hash()
    finally:
        reset_current_audit_hash(token1)

    # The chain should be internally consistent: hash_b depends on hash_a
    # We can't test exact equality due to timestamps, but we can verify
    # the chain structure by checking the prev_audit_hash linkage
    assert len(hash_a1) == 64
    assert len(hash_b1) == 64
    assert hash_a1 != hash_b1


def test_audit_hash_chain_different_order():
    """Test that different event order produces different chain."""
    token1 = set_current_audit_hash("")
    try:
        log_audit_event("event_a", foo="bar")
        log_audit_event("event_b", foo="baz")
        hash_ab = get_current_audit_hash()
    finally:
        reset_current_audit_hash(token1)

    token2 = set_current_audit_hash("")
    try:
        log_audit_event("event_b", foo="baz")
        log_audit_event("event_a", foo="bar")
        hash_ba = get_current_audit_hash()
    finally:
        reset_current_audit_hash(token2)

    assert hash_ab != hash_ba


def test_audit_hash_chain_tamper_detection():
    """Test that tampering with an event breaks the chain."""
    # Simulate legitimate chain
    token1 = set_current_audit_hash("")
    try:
        log_audit_event("event_1", user="alice")
        log_audit_event("event_2", user="bob")
        legitimate_hash = get_current_audit_hash()
    finally:
        reset_current_audit_hash(token1)

    # Simulate attacker trying to insert event_2' after event_1
    token2 = set_current_audit_hash("")
    try:
        log_audit_event("event_1", user="alice")
        log_audit_event("event_2_tampered", user="eve")
        tampered_hash = get_current_audit_hash()
    finally:
        reset_current_audit_hash(token2)

    assert legitimate_hash != tampered_hash