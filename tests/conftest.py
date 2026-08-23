import os
import sys

os.environ["TESTING"] = "1"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only-not-for-production-use-32chars"

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from core import models as core_models  # noqa: F401

_REGISTERED_MODEL_EXPORTS = core_models.__all__

postgres_container = PostgresContainer("postgres:17-alpine")
postgres_container.start()

_container_host = postgres_container.get_container_host_ip()
_container_port = str(postgres_container.get_container_host_port())
_container_user = postgres_container.username
_container_password = postgres_container.password
_container_dbname = postgres_container.dbname

_worker_id = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
_worker_db_name = f"test_{_worker_id}"

_sync_url = (
    f"postgresql+psycopg://{_container_user}:{_container_password}"
    f"@{_container_host}:{_container_port}/{_container_dbname}"
)
_sync_engine = create_engine(_sync_url)
with _sync_engine.connect() as conn:
    conn.execute(text("COMMIT"))
    conn.execute(text(f'DROP DATABASE IF EXISTS "{_worker_db_name}"'))
    conn.execute(text(f'CREATE DATABASE "{_worker_db_name}"'))
    conn.commit()
_sync_engine.dispose()

os.environ["DB_HOST"] = _container_host
os.environ["DB_PORT"] = _container_port
os.environ["DB_USER"] = _container_user
os.environ["DB_PASSWORD"] = _container_password
os.environ["DB_NAME"] = _worker_db_name

import core.config  # noqa: E402

_worker_database_url = (
    f"postgresql+asyncpg://{_container_user}:{_container_password}"
    f"@{_container_host}:{_container_port}/{_worker_db_name}"
)
core.config.settings.database_url = _worker_database_url

from app.main import app as fastapi_app  # noqa: E402
from core.database import Base, get_db  # noqa: E402

TEST_DATABASE_URL = _worker_database_url

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

TestingSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def override_get_db():
    async def inner():
        async with TestingSessionLocal() as session:
            yield session

    return inner


fastapi_app.dependency_overrides[get_db] = override_get_db()


@pytest.fixture(scope="session", autouse=True)
async def _session_cleanup():
    yield
    await test_engine.dispose()
    postgres_container.stop()


@pytest.fixture(scope="function", autouse=True)
async def setup_database():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
def _reset_shutdown_event():
    try:
        from worker.state import shutdown_event

        shutdown_event.clear()
    except Exception:
        pass

    worker_main = sys.modules.get("worker.main")
    if worker_main is not None:
        worker_main.shutdown_requested_at = None


@pytest.fixture(autouse=True)
def _disable_token_blacklist_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _not_blacklisted(_token_jti: str) -> bool:
        return False

    async def _reserve_ok(_token_jti: str, ttl_seconds: int = 0) -> bool:
        return True

    monkeypatch.setattr("app.api.dependencies.is_token_blacklisted", _not_blacklisted)
    monkeypatch.setattr("app.services.token_blacklist.reserve_token_jti", _reserve_ok)


@pytest.fixture
async def db_session() -> AsyncSession:
    async with TestingSessionLocal() as session:
        yield session


@pytest.fixture
def sample_url() -> str:
    return "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


async def create_test_user_and_login(
    client,
    email: str = "downloads@example.com",
    password: str = "securepassword123",
    _lock=None,
) -> str:
    import uuid

    unique_email = f"{uuid.uuid4().hex[:8]}@{email.split('@')[1]}"
    await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email, "password": password},
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": unique_email, "password": password},
    )
    return response.json()["access_token"]
