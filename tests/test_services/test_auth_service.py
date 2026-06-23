"""Tests for auth service (password hashing)."""

import pytest

from app.services.auth_service import hash_password, verify_password


class TestHashPassword:
    @pytest.mark.asyncio
    async def test_returns_bcrypt_hash(self):
        hashed = await hash_password("testpassword123")
        assert hashed.startswith("$2b$")
        assert hashed != "testpassword123"

    @pytest.mark.asyncio
    async def test_different_hashes_for_same_password(self):
        h1 = await hash_password("samepassword")
        h2 = await hash_password("samepassword")
        # bcrypt uses random salt, so hashes should differ
        assert h1 != h2


class TestVerifyPassword:
    @pytest.mark.asyncio
    async def test_correct_password(self):
        hashed = await hash_password("correctpassword")
        assert await verify_password("correctpassword", hashed) is True

    @pytest.mark.asyncio
    async def test_wrong_password(self):
        hashed = await hash_password("correctpassword")
        assert await verify_password("wrongpassword", hashed) is False

    @pytest.mark.asyncio
    async def test_empty_password(self):
        hashed = await hash_password("nonempty")
        assert await verify_password("", hashed) is False
