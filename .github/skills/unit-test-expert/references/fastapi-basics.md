# FastAPI Basics Testing (APIs)

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_get_user(async_client: AsyncClient):
    response = await async_client.get("/users/1")
    assert response.status_code == 200
    assert response.json()["name"] == "Alice"

def test_auth_mock(client, mocker):
    mocker.patch("app.auth.oauth.auth0.authorize_redirect", return_value={"status": "ok"})
    response = client.get("/login")
    assert response.status_code == 302
```
