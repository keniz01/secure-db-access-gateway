from auth import Principal, build_principal_from_claims


def test_principal_is_immutable_and_contains_only_trusted_claim_values() -> None:
    principal = build_principal_from_claims(
        {
            "sub": "auth0|user-123",
            "email": "alice@example.com",
            "org_id": "org-42",
            "roles": ["viewer", "admin"],
        }
    )

    assert principal == Principal(
        user_id="auth0|user-123",
        email="alice@example.com",
        org_id="org-42",
        roles=frozenset({"viewer", "admin"}),
    )
    assert principal.role == "admin"


def test_principal_rejects_missing_identity_or_tenant_claims() -> None:
    assert build_principal_from_claims({"email": "alice@example.com", "org_id": "org-42"}) is None
    principal = build_principal_from_claims({"sub": "auth0|user-123", "email": "alice@example.com"})
    assert principal is not None
    assert principal.org_id == "auth0|user-123"
    principal = build_principal_from_claims({"sub": "auth0|user-123", "org_id": "org-42"})
    assert principal is not None
    assert principal.email == "auth0|user-123"
