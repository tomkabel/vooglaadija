import os
import sys

# CRITICAL: Set environment variables BEFORE any other imports
os.environ["TESTING"] = "1"
os.environ["CLERK_SECRET_KEY"] = "test-clerk-secret-key-for-testing"
os.environ["CLERK_PUBLISHABLE_KEY"] = "pk_test_testclerkpublishablekey"

# Determine unique database URL per xdist worker to avoid race conditions
_worker_id = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
_test_db_path = os.path.abspath(f"test_{_worker_id}.db")
_test_db_url = f"sqlite+aiosqlite:///{_test_db_path}"

# Remove stale per-worker database from previous runs.
# create_all skips existing tables so a stale file with an older schema
# won't be updated to match the current model definitions.
try:
    os.remove(_test_db_path)
except OSError:
    pass

# Force reconfigure the database URL before any app imports.
# This ensures the app uses SQLite instead of PostgreSQL.
import core.config  # noqa: E402

core.config.settings.database_url = _test_db_url

from collections.abc import AsyncGenerator  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

from core import models as core_models  # noqa: E402
from core.database import Base, get_db  # noqa: E402

_REGISTERED_MODEL_EXPORTS = core_models.__all__

# Now import app - it will use the SQLite URL we set above
from app.main import app as fastapi_app  # noqa: E402

TEST_DATABASE_URL = _test_db_url


test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)

# Use async_sessionmaker for proper async session support
TestingSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


# Set up dependency override at session scope - applies to all tests
def override_get_db():
    async def inner():
        async with TestingSessionLocal() as session:
            yield session

    return inner


# Set the overrides on the FastAPI app instance
fastapi_app.dependency_overrides[get_db] = override_get_db()


@pytest.fixture(scope="session", autouse=True)
async def _session_cleanup() -> AsyncGenerator[None, None]:
    """Dispose the test engine after all tests in the worker finish."""
    yield
    await test_engine.dispose()


@pytest.fixture(scope="function", autouse=True)
async def setup_database() -> AsyncGenerator[None, None]:
    """Create tables before each test and drop after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
def _reset_shutdown_event():
    """Reset global worker shutdown state before each test.

    Some tests set shutdown_event or shutdown_requested_at to trigger shutdown behavior,
    which persists across tests in the same xdist worker process.
    """
    try:
        from worker.state import shutdown_event

        shutdown_event.clear()
    except Exception:
        pass

    worker_main = sys.modules.get("worker.main")
    if worker_main is not None:
        worker_main.shutdown_requested_at = None


@pytest.fixture(autouse=True)
def _mock_clerk_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock Clerk token verification for tests.

    Avoids real Clerk API calls during testing by mocking the
    verify_clerk_token function to return a user from the database.
    """
    import uuid

    from app.clerk_auth import verify_clerk_token

    _mock_users: dict[str, object] = {}

    async def _mock_verify(db, token):
        if token is None:
            return None
        # Mock: token value is "mock-token-<clerk_user_id>"
        if token.startswith("mock-token-"):
            clerk_user_id = token[11:]
            if clerk_user_id in _mock_users:
                return _mock_users[clerk_user_id]
            # Auto-create user on first sight
            from core.models.user import User

            user = User(
                id=uuid.UUID(int=0),
                clerk_user_id=clerk_user_id,
                email=f"{clerk_user_id}@example.com",
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            _mock_users[clerk_user_id] = user
            return user
        return None

    monkeypatch.setattr("app.clerk_auth.verify_clerk_token", _mock_verify)
    monkeypatch.setattr("app.api.dependencies.verify_clerk_token", _mock_verify)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session for tests."""
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
    """Create a test user and return a mock Clerk token.

    With Clerk auth, we use a mock token that the mocked verify_clerk_token
    will accept. Returns the mock token string.
    """
    import uuid

    unique_email = f"{uuid.uuid4().hex[:8]}@{email.split('@')[1]}"
    # Create user directly in the database with a clerk_user_id
    # The mock auth will auto-create users from the token
    clerk_user_id = f"user_{uuid.uuid4().hex[:12]}"
    return f"mock-token-{clerk_user_id}"
