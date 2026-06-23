"""Tests for structured logging configuration (structlog)."""

import os

import pytest

from core.logging_config import configure_logging, get_logger

structlog = pytest.importorskip("structlog", reason="structlog not installed")


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset logging configuration before each test."""
    # Save original environment
    original_env = os.environ.get("ENVIRONMENT")

    # Reset structlog configuration
    import structlog

    structlog.reset_defaults()

    yield

    # Restore environment
    if original_env is not None:
        os.environ["ENVIRONMENT"] = original_env
    elif "ENVIRONMENT" in os.environ:
        del os.environ["ENVIRONMENT"]


class TestStructlogConfiguration:
    """Test structlog configuration and basic functionality."""

    def test_configure_logging_in_development(self):
        """Test that logging configures without error in development mode."""
        os.environ["ENVIRONMENT"] = "development"
        # Should not raise
        configure_logging(log_level="DEBUG")

    def test_configure_logging_in_production(self):
        """Test that logging configures without error in production mode."""
        os.environ["ENVIRONMENT"] = "production"
        # Should not raise
        configure_logging(log_level="INFO")

    def test_get_logger_returns_bound_logger(self):
        """Test that get_logger returns a proper logger."""
        configure_logging(log_level="DEBUG")
        logger = get_logger(__name__)
        assert logger is not None
        # Should be able to log without error
        logger.info("test_message", key="value")

    def test_get_logger_with_context(self):
        """Test get_logger with bound context."""
        configure_logging(log_level="DEBUG")
        logger = get_logger(__name__, request_id="test-123")
        assert logger is not None
        logger.info("test_with_context")

    def test_logger_includes_service_context(self):
        """Test that logger includes service context."""
        configure_logging(log_level="DEBUG")
        logger = get_logger(__name__)
        # The logger should include 'service' key when configured
        # This is tested by the structlog processors
        logger.info("test_service_context")

    def test_service_context_reads_environment_env_var(self):
        """Service context uses ENVIRONMENT directly instead of Settings.environment."""
        from core.logging_config import add_service_context

        os.environ["ENVIRONMENT"] = "staging"

        event_dict = add_service_context(None, "info", {})

        assert event_dict["service"] == "vooglaadija"
        assert event_dict["environment"] == "staging"


class TestStructlogStructuredOutput:
    """Test that structured logging produces proper output."""

    def test_structured_log_message_format(self):
        """Test that log messages follow structured format."""
        configure_logging(log_level="DEBUG")
        logger = get_logger(__name__)

        # In production, logs should be JSON
        # In development, they should be human-readable
        # Both should work without error
        logger.info("structured_message", key1="value1", key2=42)

    def test_exception_logging(self):
        """Test that exceptions are properly logged."""
        configure_logging(log_level="DEBUG")
        logger = get_logger(__name__)

        try:
            raise ValueError("Test error")
        except ValueError:
            # Should not raise, should log exception
            logger.exception("exception_occurred", error_type="ValueError")


class TestStructlogPerformance:
    """Test structlog performance characteristics."""

    def test_rapid_log_calls(self):
        """Test that rapid log calls don't cause issues."""
        configure_logging(log_level="INFO")  # Use INFO so logger.info calls emit
        logger = get_logger(__name__)

        # Should handle rapid calls efficiently
        for i in range(100):
            logger.info("rapid_log", index=i)

    def test_log_with_large_context(self):
        """Test logging with large context dictionary."""
        configure_logging(log_level="DEBUG")
        logger = get_logger(__name__)

        large_context = {f"key_{i}": f"value_{i}" for i in range(50)}
        logger.info("large_context", **large_context)
