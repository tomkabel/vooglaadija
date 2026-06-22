"""Tests for API startup environment handling."""

import sys
import types

import pytest


@pytest.mark.unit
def test_initialize_sentry_uses_environment_env_var(monkeypatch):
    """Sentry startup uses ENVIRONMENT directly instead of Settings.environment."""
    from app.api import startup

    init_kwargs = {}

    sentry_sdk = types.ModuleType("sentry_sdk")
    sentry_sdk.init = lambda **kwargs: init_kwargs.update(kwargs)

    sentry_integrations = types.ModuleType("sentry_sdk.integrations")
    fastapi_integration = types.ModuleType("sentry_sdk.integrations.fastapi")
    sqlalchemy_integration = types.ModuleType("sentry_sdk.integrations.sqlalchemy")
    redis_integration = types.ModuleType("sentry_sdk.integrations.redis")

    class FastApiIntegration:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class SqlalchemyIntegration:
        pass

    class RedisIntegration:
        pass

    fastapi_integration.FastApiIntegration = FastApiIntegration
    sqlalchemy_integration.SqlalchemyIntegration = SqlalchemyIntegration
    redis_integration.RedisIntegration = RedisIntegration

    monkeypatch.setitem(sys.modules, "sentry_sdk", sentry_sdk)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", sentry_integrations)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.fastapi", fastapi_integration)
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk.integrations.sqlalchemy",
        sqlalchemy_integration,
    )
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.redis", redis_integration)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")

    startup.initialize_sentry("1.2.3")

    assert init_kwargs["environment"] == "production"
    assert init_kwargs["release"] == "vooglaadija@1.2.3"
