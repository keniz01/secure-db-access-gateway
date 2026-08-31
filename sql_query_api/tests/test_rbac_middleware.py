from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from middlewares.rbac_middleware import RBACMiddleware


def test_identity_header_prefixes_are_removed_before_handlers_run() -> None:
    app = FastAPI()
    app.add_middleware(RBACMiddleware)

    @app.get("/headers")
    async def headers(request: Request) -> dict:
        return {
            "user": request.headers.get("x-user-email"),
            "user_extension": request.headers.get("x-user-department"),
            "org": request.headers.get("x-org-id"),
            "tenant": request.headers.get("x-tenant-id"),
            "safe": request.headers.get("x-request-id"),
        }

    response = TestClient(app).get(
        "/headers",
        headers={
            "X-User-Email": "attacker@example.com",
            "X-User-Department": "finance",
            "X-Org-Id": "attacker-org",
            "X-Tenant-Id": "attacker-tenant",
            "X-Request-Id": "safe-request-id",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "user": None,
        "user_extension": None,
        "org": None,
        "tenant": None,
        "safe": "safe-request-id",
    }
