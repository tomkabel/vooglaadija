# API Reference

## Authentication

The REST API uses JWT bearer tokens for authentication. Obtain a token via `/api/v1/auth/login`,
then include it in subsequent requests:

```text
Authorization: Bearer <access_token>
```

Web UI routes use cookie-based authentication (access token stored in an `httpOnly` cookie).

The `/api/v1/health`, `/api/v1/health/ready`, and `/metrics` endpoints do not require JWT
authentication. `/metrics` may be IP-restricted in production deployments.

---

## Machine Authentication (Personal Access Tokens)

For headless agents, CLI tools, and the official MCP server, Vooglaadija supports
**long-lived, scoped Personal Access Tokens (PATs)**. A PAT authenticates as its
owning user but is subject to the scopes granted at creation time, so an agent can
be given read-only or narrowly-scoped access instead of a full account.

A PAT is a bearer token prefixed `vlj_pat_`. It is accepted anywhere a JWT access
token is, via the same header:

```text
Authorization: Bearer vlj_pat_xxxxxxxxxxxxxxxxxxxx
```

PATs are detected by their prefix; no signature is verified, so they remain valid
until revoked or expired. The raw token is returned **once** at creation and
cannot be recovered.

### Scopes

| Scope             | Grants                                                        |
| ----------------- | ------------------------------------------------------------- |
| `downloads:read`  | List/get/download jobs and failed-job queues.                 |
| `downloads:write` | Create, retry, replay, and delete jobs.                       |
| `keys:admin`      | Create, list, and revoke API keys (machine auth management).  |
| `*`               | Wildcard — every scope above (default for new keys).          |

When a PAT is used, every endpoint enforces the relevant scope and returns
`403 FORBIDDEN` with `Insufficient scope` otherwise. JWT sessions are treated as
holding the wildcard scope.

### Key management endpoints

| Method   | Endpoint              | Scope needed (PAT) | Description                       |
| -------- | --------------------- | ------------------ | --------------------------------- |
| `POST`   | `/api/v1/keys`        | `keys:admin`       | Create a key; returns the raw token once. |
| `GET`    | `/api/v1/keys`        | any                | List your keys (never includes the secret). |
| `DELETE` | `/api/v1/keys/{id}`   | `keys:admin`       | Revoke a key immediately.         |

Example — create a read/write key:

```bash
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"ci-pipeline","scopes":["downloads:read","downloads:write"]}'
```

Response (note `token` appears only here):

```json
{
  "id": "7e9c1a2b-3f4d-4a6b-9c0e-1f2a3b4c5d6e",
  "name": "ci-pipeline",
  "key_prefix": "vlj_pat_9f3a",
  "token": "vlj_pat_9f3a...full-token...",
  "scopes": ["downloads:read", "downloads:write"],
  "created_at": "2026-08-24T12:00:00Z",
  "expires_at": null,
  "last_used_at": null,
  "revoked_at": null,
  "is_active": true
}
```

---

## MCP Server

Vooglaadija ships an official [Model Context Protocol](https://modelcontextprotocol.io)
server in [`packages/mcp-server`](../packages/mcp-server), exposing the core API
as MCP **tools**, **resources**, and **prompts** over **stdio** and **SSE**
transports. It is dependency-free and authenticates with a PAT, so agents no
longer have to parse raw endpoints.

Configure it for Claude Desktop / Cursor:

```json
{
  "mcpServers": {
    "vooglaadija": {
      "command": "vooglaadija-mcp",
      "args": ["--transport", "stdio"],
      "env": {
        "VOOGLAADIJA_API_KEY": "vlj_pat_xxxxxxxxxxxxxxxxxxxx",
        "VOOGLAADIJA_API_BASE_URL": "http://localhost:8000"
      }
    }
  }
}
```

The server returns deterministic error envelopes
(`{ "error_code", "retryable", "suggestion" }`) so an agent can decide whether to
retry a failed tool call. See [`packages/mcp-server/README.md`](../packages/mcp-server/README.md).

---

## Web UI Routes

All web routes return HTML unless noted otherwise.

| Method   | Endpoint                       | Auth   | Description                                                |
| -------- | ------------------------------ | ------ | ---------------------------------------------------------- |
| `GET`    | `/`                            | No     | Redirect to login or dashboard                             |
| `GET`    | `/web/login`                   | No     | Login page                                                 |
| `POST`   | `/web/login`                   | No     | Login form submission (HTMX fragment + full-page fallback) |
| `GET`    | `/web/register`                | No     | Registration page                                          |
| `POST`   | `/web/register`                | No     | Registration form submission                               |
| `GET`    | `/web/demo-login`              | No     | One-click demo login and demo job priming                  |
| `POST`   | `/web/logout`                  | Cookie | Logout and redirect                                        |
| `GET`    | `/web/downloads`               | Cookie | Downloads dashboard                                        |
| `POST`   | `/web/downloads`               | Cookie | Create download (HTMX fragment)                            |
| `POST`   | `/web/downloads/full`          | Cookie | Create download (full-page fallback)                       |
| `GET`    | `/web/downloads/{id}/file`     | Cookie | Download processed file                                    |
| `DELETE` | `/web/downloads/{id}`          | Cookie | Delete download (HTMX fragment)                            |
| `GET`    | `/web/downloads/stream`        | Cookie | SSE real-time status stream                                |
| `GET`    | `/web/chaos-lab`               | No     | Chaos engineering lab page when feature-gated on           |
| `GET`    | `/web/chaos-lab/status`        | No     | Chaos flag status fragment when feature-gated on           |
| `GET`    | `/web/slides`                  | No     | Presentation slides page                                   |
| `GET`    | `/web/settings`                | Cookie | User settings page                                         |
| `POST`   | `/web/settings/username`       | Cookie | Update username                                            |
| `POST`   | `/web/settings/password`       | Cookie | Change password                                            |
| `POST`   | `/web/settings/delete-account` | Cookie | Delete account and all files                               |

---

## REST API Endpoints

### Auth

#### `POST /api/v1/auth/register`

Create a new user account.

|                  |                                                                           |
| ---------------- | ------------------------------------------------------------------------- |
| **Auth**         | No                                                                        |
| **Status Codes** | `201 Created`, `409 Conflict`, `422 Validation Error`, `429 Rate Limited` |

**Request body:**

```json
{
  "email": "user@example.com",
  "password": "securepassword123"
}
```

**Response (`201`):**

```json
{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "email": "user@example.com"
}
```

**Error response (`409`):**

```json
{
  "error": {
    "code": "RESOURCE_CONFLICT",
    "message": "Email already registered"
  }
}
```

---

#### `POST /api/v1/auth/login`

Authenticate and receive JWT tokens.

|                  |                                                                          |
| ---------------- | ------------------------------------------------------------------------ |
| **Auth**         | No                                                                       |
| **Status Codes** | `200 OK`, `401 Unauthorized`, `422 Validation Error`, `429 Rate Limited` |

**Request body:**

```json
{
  "email": "user@example.com",
  "password": "securepassword123"
}
```

**Response (`200`):**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

---

#### `POST /api/v1/auth/refresh`

Obtain a new access token using the refresh token (sent via cookie).

|                  |                              |
| ---------------- | ---------------------------- |
| **Auth**         | Refresh token cookie         |
| **Status Codes** | `200 OK`, `401 Unauthorized` |

**Response (`200`):**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

---

#### `POST /api/v1/auth/logout`

Clear auth cookies and redirect.

|                  |          |
| ---------------- | -------- |
| **Auth**         | Cookie   |
| **Status Codes** | `200 OK` |

---

### User

#### `GET /api/v1/auth/me`

Get the current authenticated user profile.

|                  |                              |
| ---------------- | ---------------------------- |
| **Auth**         | Bearer JWT                   |
| **Status Codes** | `200 OK`, `401 Unauthorized` |

**Response (`200`):**

```json
{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "email": "user@example.com"
}
```

---

### Downloads

#### `POST /api/v1/downloads`

Create a new download job.

|                  |                                                           |
| ---------------- | --------------------------------------------------------- |
| **Auth**         | Bearer JWT                                                |
| **Status Codes** | `201 Created`, `401 Unauthorized`, `422 Validation Error` |

**Request body:**

```json
{
  "url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
}
```

**Response (`201`):**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
  "created_at": "2024-01-15T10:30:00Z"
}
```

**Error response (`422`) — invalid URL:**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed"
  },
  "details": {
    "validation_errors": [
      {
        "field": "url",
        "message": "Invalid YouTube URL",
        "type": "value_error"
      }
    ]
  }
}
```

---

#### `GET /api/v1/downloads`

List the authenticated user's download jobs.

|                  |                              |
| ---------------- | ---------------------------- |
| **Auth**         | Bearer JWT                   |
| **Status Codes** | `200 OK`, `401 Unauthorized` |

**Query parameters:**

- `page` — page number (default: 1)
- `per_page` — items per page (default: 20, max: 100)

**Response (`200`):**

```json
{
  "downloads": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
      "status": "completed",
      "file_name": "video.mp4",
      "error": null,
      "retry_count": 0,
      "max_retries": 3,
      "next_retry_at": null,
      "created_at": "2024-01-15T10:30:00Z",
      "completed_at": "2024-01-15T10:32:00Z",
      "expires_at": "2024-01-16T10:32:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 1
  }
}
```

---

#### `GET /api/v1/downloads/{id}`

Get job status and details.

|                  |                                               |
| ---------------- | --------------------------------------------- |
| **Auth**         | Bearer JWT                                    |
| **Status Codes** | `200 OK`, `401 Unauthorized`, `404 Not Found` |

**Response (`200`):**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
  "created_at": "2024-01-15T10:30:00Z",
  "retry_count": 0
}
```

---

#### `GET /api/v1/downloads/{id}/file`

Download the processed file. The link is time-limited based on `FILE_EXPIRE_HOURS`.

|                  |                                                           |
| ---------------- | --------------------------------------------------------- |
| **Auth**         | Bearer JWT                                                |
| **Status Codes** | `200 OK`, `401 Unauthorized`, `404 Not Found`, `410 Gone` |

Returns the file as a binary stream with `Content-Disposition: attachment`.

---

#### `POST /api/v1/downloads/{id}/retry`

Retry a failed job.

|                  |                                                                  |
| ---------------- | ---------------------------------------------------------------- |
| **Auth**         | Bearer JWT                                                       |
| **Status Codes** | `200 OK`, `400 Bad Request`, `401 Unauthorized`, `404 Not Found` |

**Response (`200`):**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
  "status": "pending",
  "file_name": null,
  "error": null,
  "retry_count": 1,
  "max_retries": 3,
  "next_retry_at": "2024-01-15T10:35:00Z",
  "created_at": "2024-01-15T10:30:00Z",
  "completed_at": null,
  "expires_at": null
}
```

---

#### `DELETE /api/v1/downloads/{id}`

Delete a download job and its associated file.

|                  |                                                       |
| ---------------- | ----------------------------------------------------- |
| **Auth**         | Bearer JWT                                            |
| **Status Codes** | `204 No Content`, `401 Unauthorized`, `404 Not Found` |

---

### Health & Metrics

#### `GET /api/v1/health`

Service health check. Returns `200` when the API process is running.

|                  |          |
| ---------------- | -------- |
| **Auth**         | No       |
| **Status Codes** | `200 OK` |

**Response (`200`):**

```json
{
  "status": "ok"
}
```

---

#### `GET /api/v1/health/ready`

Readiness probe. Returns `200` when dependencies (database, Redis) are reachable; `503` otherwise.

|                  |                                     |
| ---------------- | ----------------------------------- |
| **Auth**         | No                                  |
| **Status Codes** | `200 OK`, `503 Service Unavailable` |

---

#### `GET /metrics`

Prometheus metrics endpoint.

|                  |                                         |
| ---------------- | --------------------------------------- |
| **Auth**         | No (may be IP-restricted in production) |
| **Status Codes** | `200 OK`                                |

Returns Prometheus exposition format. Enable with `FEATURE_METRICS_ENABLED=true`.

---

## SSE Streaming

Connect to `/web/downloads/stream` with `EventSource` to receive real-time job status updates.

```javascript
const eventSource = new EventSource(
  'http://localhost:8000/web/downloads/stream',
  { withCredentials: true }
);

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Job update:', data.status);
};
```

**Caution:** `withCredentials: true` requires the server to return
`Access-Control-Allow-Credentials: true` and a specific `Access-Control-Allow-Origin` (not `*`).
Ensure `CORS_ORIGINS` includes your frontend origin.
