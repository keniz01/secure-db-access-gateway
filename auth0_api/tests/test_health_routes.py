import pytest


@pytest.mark.asyncio
async def test_liveness_endpoint(client):
    """Liveness returns 200 regardless of session state."""
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_endpoint(client):
    """Readiness returns 200 when the app is ready to serve traffic."""
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
