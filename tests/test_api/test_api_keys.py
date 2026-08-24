"""Tests for personal access token (API key) management and PAT auth."""

from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import create_test_user_and_login


async def _auth_headers(client: AsyncClient, token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_create_api_key_returns_token_and_prefix(client: AsyncClient) -> None:
    token = await create_test_user_and_login(client)
    response = await client.post(
        "/api/v1/keys",
        headers=await _auth_headers(client, token),
        json={"name": "ci-pipeline", "scopes": ["downloads:read", "downloads:write"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "ci-pipeline"
    assert body["scopes"] == ["downloads:read", "downloads:write"]
    assert body["key_prefix"].startswith("vlj_pat_")
    # The raw token is only returned at creation time.
    assert body["token"].startswith("vlj_pat_")
    assert "key_hash" not in body


async def test_list_api_keys_excludes_secret(client: AsyncClient) -> None:
    token = await create_test_user_and_login(client)
    headers = await _auth_headers(client, token)
    await client.post(
        "/api/v1/keys",
        headers=headers,
        json={"name": "first", "scopes": ["*"]},
    )
    response = await client.get("/api/v1/keys", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 1
    for key in body["keys"]:
        assert "token" not in key
        assert "key_hash" not in key


async def test_api_key_authentication_works(client: AsyncClient) -> None:
    token = await create_test_user_and_login(client)
    headers = await _auth_headers(client, token)
    created = await client.post(
        "/api/v1/keys",
        headers=headers,
        json={"name": "pat", "scopes": ["downloads:read"]},
    )
    pat = created.json()["token"]

    # The PAT authenticates the same user on a protected endpoint.
    response = await client.get("/api/v1/keys", headers=await _auth_headers(client, pat))
    assert response.status_code == 200, response.text


async def test_revoke_api_key_invalidates_token(client: AsyncClient) -> None:
    token = await create_test_user_and_login(client)
    headers = await _auth_headers(client, token)
    created = await client.post(
        "/api/v1/keys",
        headers=headers,
        json={"name": "pat", "scopes": ["downloads:read"]},
    )
    body = created.json()
    pat = body["token"]
    key_id = body["id"]

    revoke = await client.delete(f"/api/v1/keys/{key_id}", headers=headers)
    assert revoke.status_code == 204, revoke.text

    # The revoked PAT no longer authenticates.
    response = await client.get("/api/v1/keys", headers=await _auth_headers(client, pat))
    assert response.status_code == 401, response.text


async def test_revoke_unknown_key_returns_404(client: AsyncClient) -> None:
    token = await create_test_user_and_login(client)
    response = await client.delete(
        "/api/v1/keys/00000000-0000-0000-0000-000000000000",
        headers=await _auth_headers(client, token),
    )
    assert response.status_code == 404, response.text


async def test_read_only_key_cannot_write(client: AsyncClient) -> None:
    token = await create_test_user_and_login(client)
    headers = await _auth_headers(client, token)
    created = await client.post(
        "/api/v1/keys",
        headers=headers,
        json={"name": "readonly", "scopes": ["downloads:read"]},
    )
    pat = created.json()["token"]
    pat_headers = await _auth_headers(client, pat)

    # Read is allowed.
    read = await client.get("/api/v1/downloads", headers=pat_headers)
    assert read.status_code == 200, read.text

    # Write is denied with 403.
    write = await client.post(
        "/api/v1/downloads",
        headers=pat_headers,
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert write.status_code == 403, write.text
    assert "Insufficient scope" in write.text


async def test_read_only_key_cannot_manage_keys(client: AsyncClient) -> None:
    token = await create_test_user_and_login(client)
    headers = await _auth_headers(client, token)
    created = await client.post(
        "/api/v1/keys",
        headers=headers,
        json={"name": "readonly", "scopes": ["downloads:read"]},
    )
    pat = created.json()["token"]

    response = await client.post(
        "/api/v1/keys",
        headers=await _auth_headers(client, pat),
        json={"name": "nested", "scopes": ["*"]},
    )
    assert response.status_code == 403, response.text


async def test_wildcard_key_can_write(client: AsyncClient) -> None:
    token = await create_test_user_and_login(client)
    headers = await _auth_headers(client, token)
    created = await client.post(
        "/api/v1/keys",
        headers=headers,
        json={"name": "admin", "scopes": ["*"]},
    )
    pat = created.json()["token"]
    pat_headers = await _auth_headers(client, pat)

    # Write scope is satisfied by the wildcard; the unknown id yields 404
    # (domain layer) rather than 403 (scope layer).
    write = await client.delete(
        "/api/v1/downloads/00000000-0000-0000-0000-000000000000",
        headers=pat_headers,
    )
    assert write.status_code == 404, write.text


async def test_unknown_pat_prefix_is_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/keys",
        headers=await _auth_headers(client, "vlj_pat_thisisnotreal"),
    )
    assert response.status_code == 401, response.text


async def test_invalid_scope_rejected(client: AsyncClient) -> None:
    token = await create_test_user_and_login(client)
    response = await client.post(
        "/api/v1/keys",
        headers=await _auth_headers(client, token),
        json={"name": "bad", "scopes": ["downloads:godmode"]},
    )
    assert response.status_code == 422, response.text
