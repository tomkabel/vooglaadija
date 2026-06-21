# YouTube Link Processor: Architectural Research Report

**Generated:** 2026-03-19 | **Research Type:** Architectural Analysis | **Scale:** Personal/Small
Project

---

## Executive Summary

This report provides a comprehensive architectural analysis for building a YouTube link processor
service using Python. The core functionality involves receiving a YouTube URL from a user, executing
`yt-dlp -g "$link"` to extract the media URL, and returning the result to the user.

**Key Recommendations:**

- **Backend Framework:** FastAPI (recommended over Flask and Django for API-focused projects)
- **Authentication:** JWT with python-jose and passlib
- **Containerization:** Docker with Docker Compose for local development
- **CI/CD:** GitHub Actions for automated testing and deployment
- **Testing:** pytest with pytest-asyncio and httpx
- **Deployment:** Render or Railway for simplicity; AWS ECS Fargate if AWS required

---

## 1. REST API Design Specifications

### 1.1 API Endpoints

The REST API follows resource-oriented URL design with standard HTTP methods:

| Method | Endpoint                  | Description                                | Auth Required |
| ------ | ------------------------- | ------------------------------------------ | ------------- |
| POST   | `/api/v1/process`         | Process YouTube URL and extract media info | Yes           |
| GET    | `/api/v1/status/{job_id}` | Check processing status (if async)         | Yes           |
| GET    | `/api/v1/health`          | Health check endpoint                      | No            |
| POST   | `/api/v1/auth/register`   | User registration                          | No            |
| POST   | `/api/v1/auth/login`      | User authentication (get JWT token)        | No            |
| POST   | `/api/v1/auth/refresh`    | Refresh access token                       | Yes           |

### 1.2 Request/Response Schemas

#### Process Endpoint (POST /api/v1/process)

**Request:**

```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "format": "best",
  "quality": "1080p"
}
```

**Response (Success):**

```json
{
  "success": true,
  "data": {
    "title": "Rick Astley - Never Gonna Give You Up",
    "url": "https://r5---sn-...-googlevideo.com/...",
    "format": "mp4",
    "quality": "1080p",
    "duration": 213,
    "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
  },
  "message": "Media URL extracted successfully"
}
```

**Response (Error):**

```json
{
  "success": false,
  "error": {
    "code": "INVALID_URL",
    "message": "The provided URL is not a valid YouTube link"
  }
}
```

### 1.3 HTTP Status Codes

| Status Code | Usage                                    |
| ----------- | ---------------------------------------- |
| 200         | Successful processing                    |
| 201         | Resource created (user registration)     |
| 400         | Invalid request format                   |
| 401         | Authentication required or invalid token |
| 403         | Rate limit exceeded                      |
| 422         | URL validation failed                    |
| 429         | Too many requests                        |
| 500         | Internal server error                    |
| 503         | Service temporarily unavailable          |

### 1.4 API Versioning

Use URL-based versioning: `/api/v1/`. This allows for backward-incompatible changes in future
versions without breaking existing clients.

### 1.5 Rate Limiting

Implement rate limiting to prevent abuse:

- **Authenticated users:** 100 requests per hour
- **Unauthenticated:** 10 requests per hour (if allowed)

Use Redis or in-memory storage for rate limiting counters.

---

## 2. Python Framework Comparison and Recommendation

### 2.1 Framework Overview

| Framework   | Type           | Performance       | Learning Curve | Best For               |
| ----------- | -------------- | ----------------- | -------------- | ---------------------- |
| **FastAPI** | Microframework | Very High (async) | Low-Medium     | APIs, microservices    |
| **Flask**   | Microframework | High              | Low            | Small APIs, prototypes |
| **Django**  | Full-stack     | Medium            | High           | CMS, complex web apps  |

### 2.2 Detailed Analysis

#### FastAPI

FastAPI is a modern web framework for building APIs with Python 3.6+ based on standard Python type
hints. It offers very high performance—on par with NodeJS and Go—thanks to its foundation on
Starlette and Pydantic. Key features include automatic OpenAPI documentation, native async/await
support, and built-in dependency injection.

**Strengths:**

- Highest performance among Python frameworks
- Automatic API documentation (Swagger UI, ReDoc)
- Native async support for concurrent request handling
- Pydantic integration for automatic request validation
- Type hints support with editor autocomplete

**Weaknesses:**

- Smaller ecosystem compared to Django
- Requires understanding of async programming for optimal performance

**Recommended for:** API-focused projects, microservices, high-performance requirements

#### Flask

Flask is a lightweight microframework that provides the most commonly-used core components of a web
application framework. It offers flexibility to choose other components as needed.

**Strengths:**

- Simple and flexible
- Large ecosystem of extensions
- Easy to learn
- Good for small to medium applications

**Weaknesses:**

- Manual validation required (no Pydantic-like validation)
- No built-in async support (though extensions exist)
- More boilerplate code for API documentation

**Recommended for:** Simple APIs, prototypes, when maximum flexibility is needed

#### Django

Django is a "batteries included" full-stack framework with many built-in features including ORM,
authentication, admin interface, and form handling.

**Strengths:**

- Complete solution with all features built-in
- Excellent for database-backed applications
- Large community and extensive documentation
- Built-in admin interface

**Heavy for simple API:**

- Overhead for simple API use cases
- Steeper learning curve
- Less flexible for non-standard architectures

**Recommended for:** Content-oriented websites, complex web applications with database requirements

### 2.3 Recommendation for This Project

**FastAPI is strongly recommended** for this YouTube link processor project because:

1. **API-Focused Design:** The project is primarily an API service, not a content website
1. **Performance:** FastAPI's async support handles concurrent requests efficiently when processing
   multiple YouTube links
1. **Automatic Documentation:** Swagger UI simplifies development and testing
1. **Type Safety:** Pydantic validation reduces bugs and provides clear error messages
1. **Simplicity:** More lightweight than Django while providing better API tooling than Flask
1. **Modern Python:** Uses Python 3.10+ type hints natively

### 2.4 Why Not Django for Frontend

Since you mentioned considering Django for frontend, consider these alternatives:

| Option                        | Use Case                                | Complexity |
| ----------------------------- | --------------------------------------- | ---------- |
| **FastAPI + Static Frontend** | Separate React/Vue/Svelte frontend      | Low-Medium |
| **FastAPI + HTMX**            | Server-side rendering with dynamic HTML | Low        |
| **Django Templates**          | Traditional server-rendered pages       | Medium     |
| **FastAPI + Jinja2**          | Server-side rendering with FastAPI      | Low        |

**Recommendation:** Use FastAPI for the backend API and a separate lightweight frontend (or HTMX for
simplicity). Django is unnecessary overhead for this project scope.

---

## 3. yt-dlp Integration

### 3.1 Core Functionality

The core of this project is executing `yt-dlp -g <url>` to extract direct media URLs. There are
three integration approaches:

### 3.2 Integration Method 1: Subprocess Execution (Recommended)

```python
import subprocess
import shlex
from typing import Optional

class YTDLPService:
    """Service for extracting media URLs from YouTube links."""

    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    def extract_url(self, youtube_url: str) -> dict:
        """
        Execute yt-dlp -g to extract media URL.

        Args:
            youtube_url: Valid YouTube URL

        Returns:
            Dictionary with media information
        """
        cmd = f"yt-dlp -g --no-warnings {shlex.quote(youtube_url)}"

        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=self.timeout
        )

        if result.returncode != 0:
            raise YTDLPError(f"yt-dlp failed: {result.stderr}")

        # Parse output - first line is video URL, second is audio URL (if separate)
        lines = result.stdout.strip().split('\n')

        return {
            "url": lines[0],
            "has_separate_audio": len(lines) > 1
        }
```

### 3.3 Integration Method 2: Python Library Import

```python
from yt_dlp import YoutubeDL
from typing import Optional

class YTDLPLibraryService:
    """Service using yt-dlp as Python library."""

    def __init__(self):
        self.ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'best',
            'extract_flat': False
        }

    def extract_info(self, youtube_url: str) -> dict:
        """
        Extract media information using yt-dlp library.

        Args:
            youtube_url: Valid YouTube URL

        Returns:
            Dictionary with detailed media information
        """
        with YoutubeDL(self.ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)

            return {
                "title": info.get('title'),
                "url": info.get('url'),
                "format": info.get('ext'),
                "quality": info.get('height'),
                "duration": info.get('duration'),
                "thumbnail": info.get('thumbnail')
            }
```

### 3.4 Integration Method 3: Async Subprocess

```python
import asyncio
import shutil
from typing import Optional

class AsyncYTDLPService:
    """Async service for extracting media URLs."""

    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    async def extract_url(self, youtube_url: str) -> dict:
        """Execute yt-dlp asynchronously."""
        yt_dlp_path = shutil.which('yt-dlp')

        process = await asyncio.create_subprocess_exec(
            yt_dlp_path, '-g', '--no-warnings', youtube_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            raise YTDLPError("Timeout during extraction")

        if process.returncode != 0:
            raise YTDLPError(f"yt-dlp failed: {stderr.decode()}")

        return {"url": stdout.decode().strip()}
```

### 3.5 Error Handling

```python
class YTDLPError(Exception):
    """Custom exception for yt-dlp errors."""
    pass

class InvalidURLError(YTDLPError):
    """Raised when URL is invalid."""
    pass

class ExtractionTimeoutError(YTDLPError):
    """Raised when extraction times out."""
    pass

class GeoRestrictedError(YTDLPError):
    """Raised when content is geo-restricted."""
    pass
```

### 3.6 System Dependencies

yt-dlp requires **ffmpeg** and **ffprobe** for merging video/audio and post-processing. Include in
Dockerfile:

```dockerfile
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
```

---

## 4. Authentication Design

### 4.1 JWT Authentication

FastAPI has built-in support for OAuth2 with JWT tokens. Implementation:

```python
# core/security.py
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

# Configuration
SECRET_KEY = "your-secret-key-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data: dict) -> str:
    """Create JWT refresh token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """Dependency to get current authenticated user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"}
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        token_type: str = payload.get("type")

        if username is None or token_type != "access":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # Fetch user from database
    user = await get_user_by_username(username)
    if user is None:
        raise credentials_exception

    return user
```

### 4.2 Login Endpoint

```python
# api/routes/auth.py
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str

class UserCreate(BaseModel):
    username: str
    password: str
    email: str

class UserResponse(BaseModel):
    username: str
    email: str

@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Authenticate user and return tokens."""
    user = await authenticate_user(form_data.username, form_data.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )

    access_token = create_access_token(data={"sub": user.username})
    refresh_token = create_refresh_token(data={"sub": user.username})

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@router.post("/register", response_model=UserResponse, status_code=201)
async def register(user_create: UserCreate):
    """Register a new user."""
    # Check if username exists
    existing_user = await get_user_by_username(user_create.username)
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already taken")

    # Hash password and create user
    hashed_password = get_password_hash(user_create.password)
    new_user = await create_user(
        username=user_create.username,
        email=user_create.email,
        hashed_password=hashed_password
    )

    return UserResponse(username=new_user.username, email=new_user.email)
```

### 4.3 Token Refresh

```python
@router.post("/refresh", response_model=Token)
async def refresh_token(refresh_token: str):
    """Refresh access token using refresh token."""
    try:
        payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        token_type: str = payload.get("type")

        if username is None or token_type != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Create new tokens
    access_token = create_access_token(data={"sub": username})
    new_refresh_token = create_refresh_token(data={"sub": username})

    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer"
    }
```

---

## 5. Docker and CI/CD

### 5.1 Dockerfile

```dockerfile
# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install yt-dlp
RUN pip install yt-dlp

# Copy application code
COPY . .

# Run as non-root user
RUN useradd -m appuser
USER appuser

# Expose port
EXPOSE 8000

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 5.2 Docker Compose for Development

```yaml
version: '3.8'

services:
  api:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://user:pass@db:5432/ytprocessor
      - SECRET_KEY=dev-secret-key-change-in-production
      - REDIS_URL=redis://redis:6379
    depends_on:
      - db
      - redis
    volumes:
      - ./app:/app
    command: uvicorn main:app --reload --host 0.0.0.0 --port 8000

  db:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: pass
      POSTGRES_DB: ytprocessor
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U user"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
  redis_data:
```

### 5.3 GitHub Actions CI/CD Pipeline

```yaml
name: CI/CD Pipeline

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15-alpine
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 5432:5432

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r requirements-dev.txt

      - name: Run tests with coverage
        env:
          DATABASE_URL: postgresql://test:test@localhost:5432/test
          SECRET_KEY: test-secret-key
        run: |
          pytest tests/ --cov=app --cov-report=xml

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          files: ./coverage.xml
          flags: unittests

  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build Docker image
        uses: docker/build-push-action@v5
        with:
          context: .
          push: false
          tags: yt-processor:${{ github.sha }}
          load: true

      - name: Run container health check
        run: |
          docker run -d --name yt-processor-test yt-processor:${{ github.sha }}
          sleep 5
          curl -f http://localhost:8000/api/v1/health || exit 1
          docker stop yt-processor-test
```

### 5.4 Production Deployment

For production, use multi-stage builds and secure configurations:

```dockerfile
# Production Dockerfile
FROM python:3.11-slim as builder

WORKDIR /app
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

FROM gcr.io/distroless/python3-debian11
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app
WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH"
USER nonroot
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

---

## 6. Unit Testing

### 6.1 Testing Stack

| Package          | Purpose                           |
| ---------------- | --------------------------------- |
| `pytest`         | Core testing framework            |
| `pytest-asyncio` | Async test support                |
| `pytest-cov`     | Coverage reporting                |
| `httpx`          | Async HTTP client for API testing |
| `pytest-mock`    | Mocking utilities                 |
| `faker`          | Fake data generation              |

### 6.2 Test Structure

```text
tests/
├── __init__.py
├── conftest.py              # Shared fixtures
├── test_api/
│   ├── __init__.py
│   ├── test_process.py      # Process endpoint tests
│   ├── test_auth.py         # Authentication tests
│   └── test_health.py      # Health check tests
├── test_services/
│   ├── __init__.py
│   └── test_ytdlp_service.py
└── test_core/
    ├── __init__.py
    └── test_security.py
```

### 6.3 Shared Fixtures (conftest.py)

```python
import pytest
import asyncio
from typing import AsyncGenerator
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from main import app
from database import Base, get_db
from core.security import create_access_token

# Test database URL
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
async def test_db():
    """Create test database."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()

@pytest.fixture
async def client(test_db) -> AsyncGenerator[AsyncClient, None]:
    """Create test client."""
    async def override_get_db():
        async with test_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()

@pytest.fixture
def auth_headers() -> dict:
    """Create authentication headers for testing."""
    token = create_access_token(data={"sub": "testuser"})
    return {"Authorization": f"Bearer {token}"}
```

### 6.4 API Endpoint Tests

```python
# tests/test_api/test_process.py
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_process_youtube_url_success(client, auth_headers):
    """Test successful YouTube URL processing."""
    with patch('services.ytdlp_service.YTDLPService.extract_url') as mock_extract:
        mock_extract.return_value = {
            "url": "https://example.com/video.mp4",
            "title": "Test Video",
            "format": "mp4"
        }

        response = await client.post(
            "/api/v1/process",
            json={"url": "https://www.youtube.com/watch?v=test"},
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "url" in data["data"]

@pytest.mark.asyncio
async def test_process_invalid_url(client, auth_headers):
    """Test processing with invalid URL."""
    response = await client.post(
        "/api/v1/process",
        json={"url": "not-a-youtube-url"},
        headers=auth_headers
    )

    assert response.status_code == 422

@pytest.mark.asyncio
async def test_process_requires_auth(client):
    """Test that processing requires authentication."""
    response = await client.post(
        "/api/v1/process",
        json={"url": "https://www.youtube.com/watch?v=test"}
    )

    assert response.status_code == 401

@pytest.mark.asyncio
async def test_health_endpoint(client):
    """Test health check endpoint."""
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
```

### 6.5 Service Tests

```python
# tests/test_services/test_ytdlp_service.py
import pytest
from unittest.mock import patch, MagicMock
from services.ytdlp_service import YTDLPService, YTDLPError

@pytest.fixture
def ytdlp_service():
    return YTDLPService(timeout=30)

def test_extract_url_success(ytdlp_service):
    """Test successful URL extraction."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "https://example.com/video.mp4\n"
    mock_result.stderr = ""

    with patch('subprocess.run', return_value=mock_result):
        result = ytdlp_service.extract_url("https://youtube.com/watch?v=test")

        assert result["url"] == "https://example.com/video.mp4"

def test_extract_url_failure(ytdlp_service):
    """Test URL extraction failure."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "ERROR: Invalid URL"

    with patch('subprocess.run', return_value=mock_result):
        with pytest.raises(YTDLPError) as exc_info:
            ytdlp_service.extract_url("https://youtube.com/watch?v=test")

        assert "Invalid URL" in str(exc_info.value)
```

### 6.6 Running Tests

```bash
# Run all tests with coverage
pytest tests/ --cov=app --cov-report=html --cov-report=term

# Run specific test file
pytest tests/test_api/test_process.py -v

# Run with verbose output
pytest -v --tb=short

# Run in watch mode
ptw
```

---

## 7. AWS Services for Infrastructure

### 7.1 Deployment Options Comparison

| Service             | Complexity  | Cost (Approx)       | Best For           |
| ------------------- | ----------- | ------------------- | ------------------ |
| **AWS Lightsail**   | Low         | $5-10/month         | Simple deployments |
| **AWS ECS Fargate** | Medium      | $20-50/month        | Containerized apps |
| **AWS EC2**         | Medium-High | $20+/month          | Full control       |
| **Render**          | Very Low    | Free tier available | Simplicity         |
| **Railway**         | Very Low    | Pay per use         | Quick deployment   |

### 7.2 Recommended: Render or Railway (Simplest)

For a personal/small project, avoid AWS complexity:

**Render:**

- Free tier available
- Automatic HTTPS
- GitHub integration
- Simple Dockerfile support

**Railway:**

- Pay per usage
- Simple deployment
- Good for prototypes

### 7.3 AWS ECS Fargate Setup (If AWS Required)

If AWS is required, use ECS Fargate with containers:

```yaml
# ecs-task-definition.json
{
  "family": "yt-processor",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "executionRoleArn": "arn:aws:iam::123456789:role/ecsTaskExecutionRole",
  "containerDefinitions": [
    {
      "name": "yt-processor",
      "image": "your-account/yt-processor:latest",
      "essential": true,
      "portMappings": [
        {
          "containerPort": 8000,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {"name": "DATABASE_URL", "value": "postgresql://..."},
        {"name": "SECRET_KEY", "value": "..."}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/yt-processor",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      }
    }
  ]
}
```

### 7.4 AWS Services Summary

| Service                       | Purpose                              |
| ----------------------------- | ------------------------------------ |
| **ECS Fargate**               | Container orchestration (serverless) |
| **RDS PostgreSQL**            | Database                             |
| **Application Load Balancer** | Traffic distribution                 |
| **Route 53**                  | DNS management                       |
| **CloudWatch**                | Logging and monitoring               |
| **Secrets Manager**           | API keys, credentials                |
| **ACM**                       | SSL certificates                     |

### 7.5 Minimal AWS Setup (If Required)

1. **ECS Fargate** cluster
1. **RDS PostgreSQL** (db.t3.micro for testing)
1. **Application Load Balancer** with HTTPS
1. **CloudWatch Logs** for monitoring

---

## 8. Complete Project Structure

```text
yt-downloader/
├── app/
│   ├── __init__.py
│   ├── main.py                      # FastAPI application entry point
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── process.py           # YouTube processing endpoint
│   │   │   ├── auth.py              # Authentication endpoints
│   │   │   └── health.py            # Health check
│   │   └── dependencies/
│   │       └── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py               # Configuration settings
│   │   └── security.py             # JWT and security utilities
│   ├── services/
│   │   ├── __init__.py
│   │   └── yt_dlp_service.py       # yt-dlp integration
│   ├── models/
│   │   ├── __init__.py
│   │   ├── schemas.py              # Pydantic models
│   │   └── database.py             # SQLAlchemy models
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py
│       ├── test_api/
│       ├── test_services/
│       └── test_core/
├── .env                            # Environment variables (local)
├── .env.example                    # Environment template
├── requirements.txt                # Production dependencies
├── requirements-dev.txt            # Development dependencies
├── Dockerfile                      # Production container
├── Dockerfile.dev                  # Development container
├── docker-compose.yml               # Local development
├── .github/
│   └── workflows/
│       └── ci.yml                  # CI/CD pipeline
├── .gitignore
├── pytest.ini                       # Pytest configuration
├── setup.py                        # Package setup
└── README.md                       # Project documentation
```

### 8.1 Key Files Content

**requirements.txt:**

```text
fastapi[standard]==0.115.0
uvicorn[standard]==0.32.0
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-multipart==0.0.12
pydantic-settings==2.6.0
yt-dlp==2024.10.7
sqlalchemy[asyncio]==2.0.36
asyncpg==0.30.0
redis==5.2.1
httpx==0.28.1
```

**requirements-dev.txt:**

```text
-r requirements.txt
pytest==8.3.4
pytest-asyncio==0.24.0
pytest-cov==6.0.0
pytest-mock==3.14.0
httpx==0.28.1
faker==30.8.2
black==24.10.0
isort==5.13.2
flake8==7.1.1
mypy==1.13.0
```

**main.py:**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.api.routes import process, auth, health
from app.core.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    yield
    # Shutdown

app = FastAPI(
    title="YouTube Link Processor",
    description="API for extracting media URLs from YouTube links",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(auth.router)
app.include_router(process.router)
app.include_router(health.router)

@app.get("/")
async def root():
    return {"message": "YouTube Link Processor API"}
```

---

## 9. Key Recommendations Summary

| Area                      | Recommendation                                             |
| ------------------------- | ---------------------------------------------------------- |
| **Backend Framework**     | FastAPI (best for APIs, async support, auto docs)          |
| **Authentication**        | JWT with python-jose and passlib                           |
| **yt-dlp Integration**    | Subprocess execution (simple, reliable)                    |
| **Database**              | PostgreSQL with SQLAlchemy async                           |
| **Caching/Rate Limiting** | Redis                                                      |
| **Testing**               | pytest with pytest-asyncio, httpx                          |
| **Containerization**      | Docker with multi-stage builds                             |
| **CI/CD**                 | GitHub Actions                                             |
| **Deployment**            | Render/Railway (simple) or AWS ECS Fargate                 |
| **Frontend**              | Not needed for API-only; use separate frontend if required |

---

## 10. Sources

1. [FastAPI Documentation](https://fastapi.tiangolo.com/) - Official FastAPI framework documentation
1. [Python Guide: Web Applications & Frameworks](https://docs.python-guide.org/scenarios/web/) -
   Comprehensive Python web framework overview
1. [yt-dlp GitHub Repository](https://github.com/yt-dlp/yt-dlp) - Official yt-dlp project and
   documentation
1. [FastAPI Security Tutorial](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/) - JWT
   authentication with FastAPI
1. [Docker Python Samples](https://docs.docker.com/samples/django/) - Docker configuration for
   Python applications
1. [yt-dlp Installation and Dependencies](https://github.com/yt-dlp/yt-dlp/wiki/Installation) -
   yt-dlp dependency requirements

---

## Methodology

This research was conducted by analyzing the official documentation and community resources for each
technology category. The primary sources were the official documentation sites for FastAPI, Python
Guide, and yt-dlp GitHub repository. The recommendations are based on industry best practices for
Python web development, with consideration for the specified scale (personal/small project). Where
multiple options exist, the simplest and most maintainable solution was recommended while preserving
extensibility for future needs.
