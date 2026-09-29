"""Admin API routes for access review and security auditing."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional

from auth import Principal, build_principal_from_claims, extract_bearer_token, validate_access_token
from config.app_logger import log_audit_event
from services.tenant_database_resolver import TenantDatabaseResolver
from services.policy_engine import PolicyEvaluator
from routes.sql_query_controller import _policy_evaluator

router = APIRouter(prefix="/admin", tags=["admin"])

# Admin role required
ADMIN_ROLE = "admin"


def require_admin(principal: Principal) -> Principal:
    """Dependency that requires admin role."""
    if not principal.has_role(ADMIN_ROLE):
        log_audit_event("admin_access_denied", user=principal.email, org_id=principal.org_id,
                        reason=f"Role {principal.roles} not authorized for admin access")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required"
        )
    return principal


async def get_current_principal() -> Principal:
    """Extract and validate principal from request (placeholder - would be FastAPI Depends in real implementation)."""
    # In a real implementation, this would be a FastAPI dependency that extracts
    # the principal from the request state (set by RBAC middleware)
    # For now, this is a placeholder showing the pattern
    pass


class TenantBindingResponse(BaseModel):
    """Response model for tenant database binding."""
    org_id: str
    database_id: str
    credential_mode: str
    data_schema: str
    metadata_schema: str
    host: Optional[str] = None
    port: Optional[int] = None
    db_name: Optional[str] = None
    pool_size: Optional[int] = None
    max_overflow: Optional[int] = None
    has_replica: bool = False


class OPAPolicyResponse(BaseModel):
    """Response model for OPA policy."""
    id: str
    effect: str
    org_id: Optional[str] = None
    principal_id: Optional[str] = None
    roles: list[str] = []
    database_id: Optional[str] = None
    table: Optional[str] = None
    columns: list[str] = []
    masked_columns: list[str] = []
    row_scope: dict[str, str] = {}


class AccessReviewSummary(BaseModel):
    """Summary of access review for a tenant."""
    org_id: str
    total_bindings: int
    total_policies: int
    admin_users: int
    viewer_users: int
    bindings: list[TenantBindingResponse]
    policies: list[OPAPolicyResponse]


@router.get("/access-review/{org_id}", response_model=AccessReviewSummary)
async def get_access_review(org_id: str, principal: Principal = Depends(require_admin)):
    """Get complete access review for an organization.

    Returns all tenant database bindings and OPA policies for the organization.
    Requires admin role.
    """
    log_audit_event("admin_access_review", user=principal.email, org_id=org_id,
                    action="view_access_review", admin_user=principal.email)

    # Get tenant bindings
    resolver = TenantDatabaseResolver.from_environment()
    bindings = []
    for (binding_org_id, binding_database_id), configs in resolver._bindings.items():
        if binding_org_id == org_id:
            for config in configs:
                bindings.append(TenantBindingResponse(
                    org_id=config.org_id,
                    database_id=config.database_id,
                    credential_mode=config.credential_mode,
                    data_schema=config.data_schema,
                    metadata_schema=config.metadata_schema,
                    host=config.host if config.credential_mode == "rotated" else None,
                    port=config.port if config.credential_mode == "rotated" else None,
                    db_name=config.db_name if config.credential_mode == "rotated" else None,
                    pool_size=config.pool_size,
                    max_overflow=config.max_overflow,
                    has_replica=bool(config.replica_connection_string),
                ))

    # Get OPA policies
    policies = []
    if hasattr(_policy_evaluator, "policies"):
        for policy in _policy_evaluator.policies:
            if policy.org_id is None or policy.org_id == org_id:
                policies.append(OPAPolicyResponse(
                    id=policy.id,
                    effect=policy.effect,
                    org_id=policy.org_id,
                    principal_id=policy.principal_id,
                    roles=list(policy.roles),
                    database_id=policy.database_id,
                    table=policy.table,
                    columns=list(policy.columns),
                    masked_columns=list(policy.masked_columns),
                    row_scope=dict(policy.row_scope),
                ))

    # Count roles (simplified - in real implementation would query user store)
    admin_users = 0
    viewer_users = 0

    log_audit_event("admin_access_review_completed", user=principal.email, org_id=org_id,
                    bindings_count=len(bindings), policies_count=len(policies))

    return AccessReviewSummary(
        org_id=org_id,
        total_bindings=len(bindings),
        total_policies=len(policies),
        admin_users=admin_users,
        viewer_users=viewer_users,
        bindings=bindings,
        policies=policies,
    )


@router.get("/access-review")
async def list_all_access_reviews(principal: Principal = Depends(require_admin)):
    """List access review summaries for all organizations.

    Returns a summary of all tenant bindings and policies across all organizations.
    Requires admin role.
    """
    log_audit_event("admin_list_access_reviews", user=principal.email, action="list_all_reviews")

    resolver = TenantDatabaseResolver.from_environment()
    org_summaries = {}

    for (binding_org_id, binding_database_id), configs in resolver._bindings.items():
        if binding_org_id not in org_summaries:
            org_summaries[binding_org_id] = {
                "org_id": binding_org_id,
                "total_bindings": 0,
                "databases": set(),
            }
        org_summaries[binding_org_id]["total_bindings"] += len(configs)
        org_summaries[binding_org_id]["databases"].add(binding_database_id)

    summaries = [
        {
            "org_id": org_id,
            "total_bindings": data["total_bindings"],
            "database_count": len(data["databases"]),
        }
        for org_id, data in org_summaries.items()
    ]

    log_audit_event("admin_list_access_reviews_completed", user=principal.email, count=len(summaries))
    return {"organizations": summaries}


class EffectiveAccessRequest(BaseModel):
    """Request model for checking effective access."""
    org_id: str
    database_id: str
    user_id: Optional[str] = None
    roles: list[str] = Field(default_factory=list)


@router.post("/effective-access")
async def check_effective_access(
    request: EffectiveAccessRequest,
    principal: Principal = Depends(require_admin),
):
    """Check what tables/columns a user can access in a specific database.

    Used for access review and troubleshooting. Requires admin role.
    """
    log_audit_event("admin_effective_access_check", user=principal.email, org_id=request.org_id,
                    target_user=request.user_id, database_id=request.database_id,
                    admin_user=principal.email)

    # Create a principal for the target user
    target_principal = Principal(
        user_id=request.user_id or "unknown",
        email="target@review.local",
        org_id=request.org_id,
        roles=frozenset(request.roles),
    )

    # Get effective access from policy evaluator
    effective = _policy_evaluator.effective_access(target_principal, request.database_id)

    result = {
        "org_id": request.org_id,
        "database_id": request.database_id,
        "user_id": request.user_id,
        "accessible_tables": list(effective.accessible_tables),
        "allowed_columns": list(effective.allowed_columns),
        "masked_columns": list(effective.masked_columns),
        "unrestricted": effective.unrestricted,
    }

    log_audit_event("admin_effective_access_completed", user=principal.email, org_id=request.org_id,
                    tables_count=len(effective.accessible_tables), columns_count=len(effective.allowed_columns))

    return result