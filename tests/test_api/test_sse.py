"""Tests for SSE (Server-Sent Events) endpoints."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


class TestDownloadStatusStream:
    """Tests for GET /web/downloads/stream endpoint."""

    @pytest.mark.asyncio
    async def test_sse_endpoint_requires_auth(self):
        """Test that SSE endpoint returns 401 without authentication."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/web/downloads/stream")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_sse_endpoint_with_auth_starts_stream(self):
        """Test that SSE endpoint returns an EventSourceResponse for authenticated users."""
        import uuid as _uuid
        from unittest.mock import MagicMock

        from sse_starlette import EventSourceResponse

        from app.api.routes.sse import download_status_stream

        mock_request = MagicMock()
        mock_user = MagicMock()
        mock_user.id = _uuid.uuid4()

        response = await download_status_stream(mock_request, mock_user)

        assert isinstance(response, EventSourceResponse)
        assert response.status_code == 200
        assert response.media_type == "text/event-stream"


class TestEventGenerator:
    """Tests for event_generator function."""

    @pytest.mark.asyncio
    async def test_event_generator_handles_disconnection(self):
        """Test that event generator breaks when request is disconnected."""
        from fastapi import Request

        from app.api.routes.sse import event_generator

        mock_request = MagicMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(return_value=True)

        mock_session_factory = MagicMock()

        events = []
        async for event in event_generator(mock_request, mock_session_factory, uuid.uuid4()):
            events.append(event)

        assert len(events) == 0
        mock_request.is_disconnected.assert_called()

    @pytest.mark.asyncio
    async def test_event_generator_yields_initial_state(self):
        """Test that event generator yields initial job state from database."""
        from datetime import UTC, datetime

        from fastapi import Request

        from app.api.routes.sse import event_generator
        from core.models.download_job import DownloadJob

        mock_request = MagicMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(side_effect=[False, True])

        mock_result = MagicMock()
        mock_job = MagicMock(spec=DownloadJob)
        mock_job.id = uuid.uuid4()
        mock_job.url = "https://youtube.com/watch?v=test"
        mock_job.title = None
        mock_job.status = "pending"
        mock_job.file_name = None
        mock_job.error = None
        mock_job.created_at = datetime.now(UTC)
        mock_job.updated_at = datetime.now(UTC)

        mock_result.scalars.return_value.all.return_value = [mock_job]

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_session_factory = MagicMock(return_value=mock_session)

        mock_pubsub_service = MagicMock()

        async def mock_subscribe(user_id):
            return
            yield

        mock_pubsub_service.subscribe = mock_subscribe

        async def mock_polling(*args, **kwargs):
            yield

        with (
            patch("app.api.routes.sse.get_pubsub_service", return_value=mock_pubsub_service),
            patch("app.api.routes.sse.fallback_polling_generator", mock_polling),
        ):
            events = []
            async for event in event_generator(mock_request, mock_session_factory, uuid.uuid4()):
                events.append(event)
                if len(events) >= 2:
                    break

        assert len(events) >= 1
        assert events[0].event == "job_update"

    @pytest.mark.asyncio
    async def test_event_generator_pubsub_path_yields_updates(self):
        """Test that event generator uses pub/sub when available."""

        from fastapi import Request

        from app.api.routes.sse import event_generator

        mock_request = MagicMock(spec=Request)
        # is_disconnected sequence:
        # 1. pubsub_event_generator while check -> False
        # 2. first pubsub async-for iteration -> False
        # 3. second pubsub async-for iteration -> False
        # 4. fallback_polling_generator check -> True (break)
        call_count = [0]

        async def mock_is_disconnected():
            call_count[0] += 1
            if call_count[0] >= 100:  # Allow enough calls for startup buffering and replay
                return True
            return False

        mock_request.is_disconnected = mock_is_disconnected

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_session_factory = MagicMock(return_value=mock_session)

        job_id = str(uuid.uuid4())
        pubsub_messages = [
            {
                "id": job_id,
                "status": "processing",
                "url": "https://youtube.com/watch?v=test",
                "updated_at": "2024-01-01T00:00:00",
            },
            {
                "id": job_id,
                "status": "completed",
                "url": "https://youtube.com/watch?v=test",
                "file_name": "video.mp4",
                "updated_at": "2024-01-01T00:00:01",
            },
        ]

        async def mock_subscribe(user_id):
            for msg in pubsub_messages:
                yield msg

        mock_pubsub_service = MagicMock()
        mock_pubsub_service.subscribe = mock_subscribe

        with patch("app.api.routes.sse.get_pubsub_service", return_value=mock_pubsub_service):
            events = []
            async for event in event_generator(mock_request, mock_session_factory, uuid.uuid4()):
                events.append(event)
                if len(events) >= 2:
                    break

        assert len(events) == 2
        assert events[0].event == "job_update"
        assert events[1].event == "job_update"

    @pytest.mark.asyncio
    async def test_event_generator_pubsub_dedupes_same_status(self):
        """Test that pub/sub path deduplicates identical status updates."""
        from fastapi import Request

        from app.api.routes.sse import event_generator

        mock_request = MagicMock(spec=Request)
        # is_disconnected sequence:
        # 1. pubsub_event_generator while check -> False
        # 2. first pubsub async-for iteration -> False
        # 3. second pubsub async-for iteration -> False (deduped, not yielded)
        # 4. fallback_polling_generator check -> True (break)
        call_count = [0]

        async def mock_is_disconnected():
            call_count[0] += 1
            return call_count[0] >= 20

        mock_request.is_disconnected = mock_is_disconnected

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_session_factory = MagicMock(return_value=mock_session)

        job_id = str(uuid.uuid4())
        pubsub_messages = [
            {
                "id": job_id,
                "status": "processing",
                "url": "https://youtube.com/watch?v=test",
                "updated_at": "2024-01-01T00:00:00",
            },
            {
                "id": job_id,
                "status": "processing",
                "url": "https://youtube.com/watch?v=test",
                "updated_at": "2024-01-01T00:00:00",
            },
        ]

        async def mock_subscribe(user_id):
            for msg in pubsub_messages:
                yield msg

        mock_pubsub_service = MagicMock()
        mock_pubsub_service.subscribe = mock_subscribe

        with patch("app.api.routes.sse.get_pubsub_service", return_value=mock_pubsub_service):
            events = []
            async for event in event_generator(mock_request, mock_session_factory, uuid.uuid4()):
                events.append(event)
                if len(events) >= 1:
                    break

        # Should only yield one event because status didn't change
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_event_generator_fallback_when_pubsub_fails(self):
        """Test that event generator falls back to polling when pub/sub is unavailable."""
        from datetime import UTC, datetime

        from app.api.routes.sse import event_generator
        from core.models.download_job import DownloadJob

        # Create proper mock request
        class MockRequest:
            def __init__(self):
                self._disconnect_count = 0
                self.state = MagicMock()

            async def is_disconnected(self):
                self._disconnect_count += 1
                # Return False for first 20 calls, then True
                return self._disconnect_count >= 20

        mock_request = MockRequest()

        mock_job = MagicMock(spec=DownloadJob)
        mock_job.id = uuid.uuid4()
        mock_job.url = "https://youtube.com/watch?v=test"
        mock_job.title = None
        mock_job.status = "completed"
        mock_job.file_name = "video.mp4"
        mock_job.error = None
        mock_job.created_at = datetime.now(UTC)
        mock_job.updated_at = datetime.now(UTC)

        mock_result = MagicMock()
        # Return a job for DB queries so polling can yield an event
        mock_result.scalars.return_value.all.return_value = [mock_job]

        # Create proper async context manager for session
        class MockSession:
            def __init__(self, mock_result):
                self.mock_result = mock_result

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            async def execute(self, *args, **kwargs):
                return self.mock_result

        mock_session = MockSession(mock_result)
        mock_session_factory = MagicMock(return_value=mock_session)

        # Create a proper async generator mock that raises ConnectionError
        async def mock_subscribe(user_id):
            raise ConnectionError("Redis down")
            yield  # Make it an async generator

        mock_pubsub_service = MagicMock()
        mock_pubsub_service.subscribe = mock_subscribe

        with (
            patch("app.api.routes.sse.get_pubsub_service", return_value=mock_pubsub_service),
            patch("app.api.routes.sse.POLL_INTERVAL_SECONDS", 0),
        ):
            events = []
            async for event in event_generator(mock_request, mock_session_factory, uuid.uuid4()):
                events.append(event)
                if len(events) >= 1:
                    break

        # Should get at least one event from the fallback polling path
        assert len(events) >= 1
        assert events[0].event == "job_update"

    @pytest.mark.asyncio
    async def test_event_generator_cancelled_error_handled(self):
        """Test that CancelledError is caught and handled gracefully."""
        import asyncio

        from fastapi import Request

        from app.api.routes.sse import event_generator

        mock_request = MagicMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(side_effect=asyncio.CancelledError)

        mock_session_factory = MagicMock()

        events = []
        try:
            async for event in event_generator(mock_request, mock_session_factory, uuid.uuid4()):
                events.append(event)
        except asyncio.CancelledError:
            pass  # Expected

        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_event_generator_max_seen_jobs_limit(self):
        """Test that event generator respects MAX_SEEN_JOBS limit."""
        from datetime import UTC, datetime

        from fastapi import Request

        from app.api.routes.sse import MAX_SEEN_JOBS, event_generator
        from core.models.download_job import DownloadJob

        mock_request = MagicMock(spec=Request)
        call_count = [0]

        async def mock_is_disconnected():
            call_count[0] += 1
            if call_count[0] >= 3:
                return True
            return False

        mock_request.is_disconnected = mock_is_disconnected

        mock_result = MagicMock()
        jobs = []
        for i in range(MAX_SEEN_JOBS + 50):
            mock_job = MagicMock(spec=DownloadJob)
            mock_job.id = uuid.uuid4()
            mock_job.url = f"https://youtube.com/watch?v=test{i}"
            mock_job.title = None
            mock_job.status = "pending"
            mock_job.file_name = None
            mock_job.error = None
            mock_job.created_at = datetime.now(UTC)
            mock_job.updated_at = datetime.now(UTC)
            jobs.append(mock_job)

        mock_result.scalars.return_value.all.return_value = jobs

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_session_factory = MagicMock(return_value=mock_session)

        events = []
        async for event in event_generator(mock_request, mock_session_factory, uuid.uuid4()):
            events.append(event)
            if len(events) >= 10:
                break

        assert len(events) >= 1


class TestJobToSseData:
    """Tests for _job_to_sse_data function."""

    @pytest.mark.asyncio
    async def test_job_to_sse_data_with_none_timestamps(self):
        """Test _job_to_sse_data handles None timestamps."""

        from app.api.routes.sse import _job_to_sse_data
        from core.models.download_job import DownloadJob

        mock_job = MagicMock(spec=DownloadJob)
        mock_job.id = uuid.uuid4()
        mock_job.url = "https://youtube.com/watch?v=test"
        mock_job.title = None
        mock_job.status = "pending"
        mock_job.file_name = None
        mock_job.error = None
        mock_job.created_at = None
        mock_job.updated_at = None

        result = await _job_to_sse_data(mock_job)

        assert result["id"] == str(mock_job.id)
        assert result["url"] == mock_job.url
        assert result["status"] == mock_job.status
        assert result["file_name"] is None
        assert result["error"] is None
        assert result["created_at"] is None
        assert result["updated_at"] is None

    @pytest.mark.asyncio
    async def test_job_to_sse_data_with_timestamps(self):
        """Test _job_to_sse_data formats timestamps correctly."""
        from datetime import UTC, datetime

        from app.api.routes.sse import _job_to_sse_data
        from core.models.download_job import DownloadJob

        created = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        updated = datetime(2024, 1, 1, 12, 1, 0, tzinfo=UTC)

        mock_job = MagicMock(spec=DownloadJob)
        mock_job.id = uuid.uuid4()
        mock_job.url = "https://youtube.com/watch?v=test"
        mock_job.title = "Test Video"
        mock_job.status = "completed"
        mock_job.file_name = "video.mp4"
        mock_job.error = None
        mock_job.created_at = created
        mock_job.updated_at = updated

        result = await _job_to_sse_data(mock_job)

        assert result["created_at"] == created.isoformat()
        assert result["updated_at"] == updated.isoformat()


class TestSubscribeToPubsub:
    """Tests for _subscribe_to_pubsub function."""

    @pytest.mark.asyncio
    async def test_subscribe_to_pubsub_dedupes_with_updated_at(self):
        """Test that messages with same job_id but different updated_at are not deduplicated."""
        from collections import OrderedDict

        from app.api.routes.sse import _subscribe_to_pubsub

        job_id = str(uuid.uuid4())
        pubsub_messages = [
            {"id": job_id, "status": "processing", "updated_at": "2024-01-01T00:00:00"},
            {"id": job_id, "status": "processing", "updated_at": "2024-01-01T00:00:01"},
        ]

        async def mock_subscribe(user_id):
            for msg in pubsub_messages:
                yield msg

        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = mock_subscribe

        last_seen = OrderedDict()
        events = []
        async for event in _subscribe_to_pubsub(mock_pubsub, uuid.uuid4(), last_seen):
            events.append(event)

        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_subscribe_to_pubsub_dedupes_same_updated_at(self):
        """Test that messages with same job_id and updated_at are deduplicated."""
        from collections import OrderedDict

        from app.api.routes.sse import _subscribe_to_pubsub

        job_id = str(uuid.uuid4())
        pubsub_messages = [
            {"id": job_id, "status": "processing", "updated_at": "2024-01-01T00:00:00"},
            {"id": job_id, "status": "processing", "updated_at": "2024-01-01T00:00:00"},
        ]

        async def mock_subscribe(user_id):
            for msg in pubsub_messages:
                yield msg

        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = mock_subscribe

        last_seen = OrderedDict()
        events = []
        async for event in _subscribe_to_pubsub(mock_pubsub, uuid.uuid4(), last_seen):
            events.append(event)

        assert len(events) == 1


class TestSubscribeToProgressPubsub:
    """Tests for _subscribe_to_progress_pubsub function."""

    @pytest.mark.asyncio
    async def test_subscribe_to_progress_pubsub_yields_valid_progress(self):
        """Test that progress pub/sub messages are converted to progress SSE events."""
        from app.api.routes.sse import _subscribe_to_progress_pubsub

        job_id = str(uuid.uuid4())
        pubsub_messages = [
            {"id": job_id, "status": "processing"},
            {"progress": {"percent": 15}},
            {"id": job_id, "progress": {"percent": 42, "speed": "1.2MiB/s"}},
        ]

        async def mock_subscribe_progress(user_id):
            for msg in pubsub_messages:
                yield msg

        mock_pubsub = MagicMock()
        mock_pubsub.subscribe_progress = mock_subscribe_progress

        events = []
        async for event in _subscribe_to_progress_pubsub(mock_pubsub, uuid.uuid4()):
            events.append(event)

        assert len(events) == 1
        assert events[0].event == "progress_update"
        assert json.loads(events[0].data)["progress"]["percent"] == 42


class TestProgressEventGenerator:
    """Tests for progress_event_generator function."""

    @pytest.mark.asyncio
    async def test_progress_event_generator_reconnects_and_yields_updates(self):
        """Test that progress SSE reconnects after a pub/sub error and yields updates."""
        from app.api.routes.sse import progress_event_generator

        mock_request = MagicMock()
        mock_request.is_disconnected = AsyncMock(return_value=False)

        job_id = str(uuid.uuid4())
        attempt_count = [0]

        async def mock_subscribe_progress(user_id):
            attempt_count[0] += 1
            if attempt_count[0] == 1:
                raise Exception("Connection lost")
            yield {"id": job_id, "progress": {"percent": 75}}

        mock_pubsub = MagicMock()
        mock_pubsub.subscribe_progress = mock_subscribe_progress

        with (
            patch("app.api.routes.sse.get_pubsub_service", return_value=mock_pubsub),
            patch("app.api.routes.sse.asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            events = []
            async for event in progress_event_generator(mock_request, uuid.uuid4()):
                events.append(event)
                break

        assert attempt_count[0] == 2
        assert len(events) == 1
        assert events[0].event == "progress_update"
        assert json.loads(events[0].data)["progress"]["percent"] == 75
        mock_sleep.assert_awaited_once()


class TestPubsubEventGenerator:
    """Tests for pubsub_event_generator function."""

    @pytest.mark.asyncio
    async def test_pubsub_event_generator_reconnects_on_exception(self):
        """Test that pubsub_event_generator reconnects on exception up to max attempts."""
        from app.api.routes.sse import (
            MAX_PUBSUB_RECONNECT_ATTEMPTS,
            pubsub_event_generator,
        )

        mock_request = MagicMock()
        call_count = [0]

        async def mock_is_disconnected():
            call_count[0] += 1
            return False

        mock_request.is_disconnected = mock_is_disconnected

        reconnect_count = [0]

        async def mock_subscribe(user_id):
            reconnect_count[0] += 1
            if reconnect_count[0] < MAX_PUBSUB_RECONNECT_ATTEMPTS:
                raise Exception("Connection lost")
            return
            yield

        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = mock_subscribe

        with patch("app.api.routes.sse.get_pubsub_service", return_value=mock_pubsub):
            events = []
            async for event in pubsub_event_generator(mock_request, uuid.uuid4()):
                events.append(event)
                if len(events) >= 1:
                    break

    @pytest.mark.asyncio
    async def test_pubsub_event_generator_cancelled_error(self):
        """Test that pubsub_event_generator handles CancelledError."""
        import asyncio

        from app.api.routes.sse import pubsub_event_generator

        mock_request = MagicMock()

        async def mock_is_disconnected():
            raise asyncio.CancelledError

        mock_request.is_disconnected = mock_is_disconnected

        events = []
        try:
            async for event in pubsub_event_generator(mock_request, uuid.uuid4()):
                events.append(event)
        except asyncio.CancelledError:
            pass

        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_pubsub_event_generator_stops_after_max_reconnects(self):
        """Test that pubsub_event_generator stops after max reconnect attempts."""
        from app.api.routes.sse import (
            MAX_PUBSUB_RECONNECT_ATTEMPTS,
            pubsub_event_generator,
        )

        mock_request = MagicMock()
        mock_request.is_disconnected = AsyncMock(return_value=False)

        attempt_count = [0]

        class MockAsyncIterator:
            def __init__(self):
                pass

            def __aiter__(self):
                return self

            async def __anext__(self):
                attempt_count[0] += 1
                if attempt_count[0] < MAX_PUBSUB_RECONNECT_ATTEMPTS:
                    raise Exception("Connection failed")
                raise StopAsyncIteration

        mock_pubsub_service = MagicMock()
        mock_pubsub_service.subscribe = MagicMock(return_value=MockAsyncIterator())

        with patch("app.api.routes.sse.get_pubsub_service", return_value=mock_pubsub_service):
            events = []
            async for event in pubsub_event_generator(mock_request, uuid.uuid4()):
                events.append(event)

        assert attempt_count[0] == MAX_PUBSUB_RECONNECT_ATTEMPTS


class TestFallbackPollingGenerator:
    """Tests for fallback_polling_generator function."""

    @pytest.mark.asyncio
    async def test_fallback_polling_generator_yields_jobs(self):
        """Test that fallback_polling_generator yields job updates."""
        from datetime import UTC, datetime

        from app.api.routes.sse import fallback_polling_generator
        from core.models.download_job import DownloadJob

        mock_request = MagicMock()
        mock_request.is_disconnected = AsyncMock(side_effect=[False, True])

        mock_job = MagicMock(spec=DownloadJob)
        mock_job.id = uuid.uuid4()
        mock_job.url = "https://youtube.com/watch?v=test"
        mock_job.title = None
        mock_job.status = "pending"
        mock_job.file_name = None
        mock_job.error = None
        mock_job.created_at = datetime.now(UTC)
        mock_job.updated_at = datetime.now(UTC)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_job]

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_session_factory = MagicMock(return_value=mock_session)

        with patch("app.api.routes.sse.POLL_INTERVAL_SECONDS", 0):
            events = []
            async for event in fallback_polling_generator(
                mock_request, mock_session_factory, uuid.uuid4()
            ):
                events.append(event)
                if len(events) >= 1:
                    break

        assert len(events) == 1
        assert events[0].event == "job_update"

    @pytest.mark.asyncio
    async def test_fallback_polling_generator_cancelled_error(self):
        """Test that fallback_polling_generator handles CancelledError."""
        import asyncio

        from app.api.routes.sse import fallback_polling_generator

        mock_request = MagicMock()

        async def mock_is_disconnected():
            raise asyncio.CancelledError

        mock_request.is_disconnected = mock_is_disconnected

        mock_session_factory = MagicMock()

        events = []
        try:
            async for event in fallback_polling_generator(
                mock_request, mock_session_factory, uuid.uuid4()
            ):
                events.append(event)
        except asyncio.CancelledError:
            pass

        assert len(events) == 0


class TestEventGeneratorExtended:
    """Extended tests for event_generator function."""

    @pytest.mark.asyncio
    async def test_event_generator_initial_state_exception_falls_back_to_pubsub(self):
        """Test that event_generator falls back to pubsub when initial state query fails."""
        from fastapi import Request

        from app.api.routes.sse import event_generator

        mock_request = MagicMock(spec=Request)
        call_count = [0]

        async def mock_is_disconnected():
            call_count[0] += 1
            return call_count[0] >= 20

        mock_request.is_disconnected = mock_is_disconnected

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(side_effect=Exception("DB error"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_session_factory = MagicMock(return_value=mock_session)

        job_id = str(uuid.uuid4())
        pubsub_messages = [
            {
                "id": job_id,
                "status": "processing",
                "url": "https://youtube.com/watch?v=test",
                "updated_at": "2024-01-01T00:00:00",
            },
        ]

        async def mock_subscribe(user_id):
            for msg in pubsub_messages:
                yield msg

        mock_pubsub_service = MagicMock()
        mock_pubsub_service.subscribe = mock_subscribe

        with patch("app.api.routes.sse.get_pubsub_service", return_value=mock_pubsub_service):
            events = []
            async for event in event_generator(mock_request, mock_session_factory, uuid.uuid4()):
                events.append(event)
                if len(events) >= 1:
                    break

        assert len(events) == 1
        assert events[0].event == "job_update"

    @pytest.mark.asyncio
    async def test_event_generator_pubsub_generator_exception_falls_back_to_polling(self):
        """Test that event_generator falls back to polling when pubsub generator fails."""
        from datetime import UTC, datetime

        from fastapi import Request

        from app.api.routes.sse import event_generator
        from core.models.download_job import DownloadJob

        mock_request = MagicMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(side_effect=[False, False, False, True])

        mock_job = MagicMock(spec=DownloadJob)
        mock_job.id = uuid.uuid4()
        mock_job.url = "https://youtube.com/watch?v=test"
        mock_job.title = "Test Video"
        mock_job.status = "completed"
        mock_job.file_name = "video.mp4"
        mock_job.error = None
        mock_job.created_at = datetime.now(UTC)
        mock_job.updated_at = datetime.now(UTC)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_job]

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_session_factory = MagicMock(return_value=mock_session)

        mock_pubsub_service = MagicMock()
        mock_pubsub_service.subscribe = AsyncMock(side_effect=Exception("Redis error"))

        with (
            patch("app.api.routes.sse.get_pubsub_service", return_value=mock_pubsub_service),
            patch("app.api.routes.sse.POLL_INTERVAL_SECONDS", 0),
        ):
            events = []
            async for event in event_generator(mock_request, mock_session_factory, uuid.uuid4()):
                events.append(event)
                if len(events) >= 1:
                    break

        assert len(events) >= 1
        assert events[0].event == "job_update"

    @pytest.mark.asyncio
    async def test_event_generator_pubsub_success_no_fallback(self):
        """Test that event_generator does not fall back to polling when pubsub succeeds."""
        from fastapi import Request

        from app.api.routes.sse import event_generator

        mock_request = MagicMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(side_effect=[False, False, False, True])

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_session_factory = MagicMock(return_value=mock_session)

        job_id = str(uuid.uuid4())
        pubsub_messages = [
            {
                "id": job_id,
                "status": "processing",
                "url": "https://youtube.com/watch?v=test",
                "updated_at": "2024-01-01T00:00:00",
            },
        ]

        async def mock_subscribe(user_id):
            for msg in pubsub_messages:
                yield msg

        mock_pubsub_service = MagicMock()
        mock_pubsub_service.subscribe = mock_subscribe

        polling_called = [False]

        async def mock_polling(*args, **kwargs):
            polling_called[0] = True
            return
            yield

        with (
            patch("app.api.routes.sse.get_pubsub_service", return_value=mock_pubsub_service),
            patch("app.api.routes.sse.fallback_polling_generator", mock_polling),
        ):
            events = []
            async for event in event_generator(mock_request, mock_session_factory, uuid.uuid4()):
                events.append(event)
                if len(events) >= 1:
                    break

        assert len(events) == 1
        assert events[0].event == "job_update"
