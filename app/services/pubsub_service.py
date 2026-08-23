"""Redis Pub/Sub service for job status broadcasting.

This module provides a Redis-based pub/sub mechanism for real-time
job status updates, replacing the polling-based SSE implementation.
"""

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from core.logging_config import get_logger
from core.redis_client import get_pubsub_redis_client

logger = get_logger(__name__)

CHANNEL_PREFIX = "job_status"
PROGRESS_CHANNEL_PREFIX = "job_progress"


class PubSubService:
    """Redis Pub/Sub service for job status and progress broadcasting.

    This service provides:
    - publish_job_status(): Publishes job status updates to user-specific channels
    - publish_job_progress(): Publishes download progress updates to a separate channel
    - subscribe(): Async generator that yields job status updates from Redis pub/sub
    - subscribe_progress(): Async generator that yields download progress updates

    Channel Patterns:
      - job_status:{user_id} — status transitions (pending/processing/completed/failed)
      - job_progress:{user_id} — download progress (percent/speed/ETA)
    """

    async def get_client(self) -> Any:
        """Get the Redis client dedicated to pub/sub.

        A dedicated, timeout-free client is used so that idle subscriptions are
        not killed by the shared client's short socket timeout.

        Returns:
            Redis client instance.

        """
        return get_pubsub_redis_client()

    async def close(self) -> None:
        """Clear PubSubService wrapper state without closing the shared client."""

    def get_channel_for_user(self, user_id: uuid.UUID) -> str:
        """Get the pub/sub channel name for a user.

        Args:
            user_id: The user's UUID.

        Returns:
            Channel name in format 'job_status:{user_id}'.

        """
        return f"{CHANNEL_PREFIX}:{user_id}"

    def get_progress_channel_for_user(self, user_id: uuid.UUID) -> str:
        """Get the pub/sub channel name for progress updates.

        Args:
            user_id: The user's UUID.

        Returns:
            Channel name in format 'job_progress:{user_id}'.

        """
        return f"{PROGRESS_CHANNEL_PREFIX}:{user_id}"

    async def publish_job_status(self, user_id: uuid.UUID, job_data: dict) -> int:
        """
        Publish a job status update to the user's status channel.

        Parameters:
            user_id (uuid.UUID): The user whose channel receives the update.
            job_data (dict): Job status data to publish.

        Returns:
            int: The number of subscribers that received the update.
        """
        client = await self.get_client()
        channel = self.get_channel_for_user(user_id)
        message = json.dumps(job_data, default=str)
        result = await client.publish(channel, message)

        logger.debug(
            "pubsub_message_published",
            channel=channel,
            job_id=job_data.get("id"),
            status=job_data.get("status"),
            subscribers=result,
        )

        return int(result)

    async def publish_job_progress(self, user_id: uuid.UUID, job_data: dict) -> int:
        """Publish a download progress update to a user's progress channel.

        Args:
            user_id: The user's UUID to publish to.
            job_data: Dictionary containing job_id and progress info (percent, speed, eta, etc.)

        Returns:
            Number of subscribers that received the message.

        """
        client = await self.get_client()
        channel = self.get_progress_channel_for_user(user_id)
        message = json.dumps(job_data, default=str)
        result = await client.publish(channel, message)

        logger.debug(
            "pubsub_progress_published",
            channel=channel,
            job_id=job_data.get("id"),
            percent=job_data.get("progress", {}).get("percent"),
            subscribers=result,
        )

        return int(result)

    async def _check_pool_health(self) -> bool:
        """Check shared Redis connection pool utilization.

        Returns True if pool has sufficient headroom. Logs a warning
        if >80% of connections are in use, which can happen under
        reconnect storms with many concurrent SSE subscriptions.
        Falls back silently on any error (fail-open).
        """
        try:
            client = await self.get_client()
            pool = client.connection_pool
            free = getattr(pool, "_available_connections", None)
            in_use = getattr(pool, "_in_use_connections", None)
            if free is not None and in_use is not None:
                total = len(free) + len(in_use)
                if total > 0 and (len(in_use) / total) > 0.8:
                    logger.warning(
                        "pubsub_pool_near_capacity",
                        used=len(in_use),
                        total=total,
                    )
                    return False
            return True
        except Exception:
            # Fail-open on introspection errors
            return True

    async def _listen(
        self,
        channel: str,
        log_name: str,
        yield_raw: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """
        Listen for messages on a Redis Pub/Sub channel and decode their JSON payloads.

        Args:
            channel: The Redis Pub/Sub channel to subscribe to.
            log_name: Label used to identify the subscription in logs.
            yield_raw: Whether to yield non-dictionary JSON payloads in a fallback dictionary.

        Yields:
            Decoded dictionary payloads, or fallback dictionaries for non-dictionary
            payloads when `yield_raw` is `True`. Invalid JSON and skipped payloads
            produce no yielded value.
        """
        client = await self.get_client()
        if not await self._check_pool_health():
            logger.warning(
                "pubsub_pool_low_creating_subscription_anyway",
                channel=channel,
                name=log_name,
            )
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)

        logger.debug("pubsub_subscription_started", channel=channel, name=log_name)

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    if isinstance(data, dict):
                        logger.debug(
                            "pubsub_message_received",
                            channel=message["channel"],
                            name=log_name,
                            job_id=data.get("id"),
                        )
                        yield data
                    elif yield_raw:
                        logger.warning(
                            "pubsub_non_dict_payload",
                            channel=message["channel"],
                            name=log_name,
                            payload_type=type(data).__name__,
                            payload=str(data)[:200],
                        )
                        yield {"job_id": None, "_raw": data}
                    else:
                        logger.warning(
                            "pubsub_non_dict_payload_skipped",
                            channel=message["channel"],
                            name=log_name,
                            payload_type=type(data).__name__,
                        )
                except json.JSONDecodeError as e:
                    logger.error(
                        "pubsub_invalid_json",
                        channel=message["channel"],
                        name=log_name,
                        error=str(e),
                        data=message["data"][:200],
                    )
        finally:
            try:
                await pubsub.unsubscribe(channel)
            except Exception as e:
                logger.exception(
                    "pubsub_unsubscribe_failed",
                    channel=channel,
                    name=log_name,
                    error=str(e),
                )
            await pubsub.close()
            logger.debug("pubsub_subscription_ended", channel=channel, name=log_name)

    def subscribe(self, user_id: uuid.UUID) -> AsyncGenerator[dict, None]:
        """Subscribe to a user's job status channel."""
        channel = self.get_channel_for_user(user_id)
        return self._listen(channel, "status", yield_raw=True)

    def subscribe_progress(self, user_id: uuid.UUID) -> AsyncGenerator[dict, None]:
        """Subscribe to a user's download progress channel."""
        channel = self.get_progress_channel_for_user(user_id)
        return self._listen(channel, "progress", yield_raw=False)

    async def health_check(self) -> bool:
        """Check if Redis connection is healthy.

        Returns:
            True if Redis is reachable, False otherwise.

        """
        try:
            client = await self.get_client()
            await client.ping()
            return True
        except Exception as e:
            logger.error("pubsub_health_check_failed", error=str(e))
            return False


_pubsub_service: PubSubService | None = None


def get_pubsub_service() -> PubSubService:
    """Get the global PubSubService instance.

    Returns:
        The global PubSubService instance.

    """
    global _pubsub_service
    if _pubsub_service is None:
        _pubsub_service = PubSubService()
    return _pubsub_service


async def close_pubsub_service() -> None:
    """Clear the global PubSubService wrapper instance."""
    global _pubsub_service
    if _pubsub_service is not None:
        await _pubsub_service.close()
        _pubsub_service = None
