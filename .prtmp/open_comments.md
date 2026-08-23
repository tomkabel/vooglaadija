====================================================================================================
ID 3487304543 | Copilot | app/services/download_service.py | line None orig 226 | OUTDATED
`get_file_path()` checks disk existence before expiry. This causes expired jobs whose file has already been cleaned up (or never existed on this node) to return a missing-file error instead of the intended 410/expired behavior (see existing tests that set an expired job with a fake /tmp path). Move the expiry check to run before `os.path.isfile(...)` so expiration wins regardless of disk state.

====================================================================================================
ID 3487304546 | Copilot | app/services/yt_dlp_service.py | line 322 orig 308 | OUTDATED
The subdomain-bypass detection uses `if domain in hostname`, which will misclassify unrelated domains that merely contain a platform domain as a substring (e.g. `myyoutube.com` contains `youtube.com`) as `unknown` instead of falling through to the default `youtube` behavior. Tighten this check to only match the bypass pattern where the platform domain is followed by a dot (e.g. `youtube.com.evil.com`).

====================================================================================================
ID 3487304549 | Copilot | worker/browser_executor.py | line None orig 313 | OUTDATED
Non-JSON error responses are always classified as TRANSIENT, even when the HTTP status implies a terminal category (e.g. 404 should map to NOT_FOUND per the PR’s error mapping). Consider mapping based on the synthesized `http_<status>` signal so 404/429/etc are categorized consistently even when the body is empty or non-JSON.

====================================================================================================
ID 3487304557 | Copilot | worker/browser_executor.py | line None orig 324 | OUTDATED
If the microservice returns a JSON error payload without an `error` field, the code currently falls back to `unknown_error` and loses the HTTP status context (e.g. a 404 could become TRANSIENT). Preserve the synthesized `http_<status>` signal by defaulting `payload['error']` to it before calling `_parse_failure_payload()`.

====================================================================================================
ID 3487304561 | Copilot | worker/browser_executor.py | line None orig 366 | OUTDATED
`_map_response_to_category()` treats all `http_4xx` codes as BLOCKED (except 429). This makes `http_404` classify as BLOCKED, conflicting with the documented mapping (404 → NOT_FOUND). Add an explicit `http_404` case before the generic 4xx block.

====================================================================================================
ID 3487304573 | Copilot | app/api/routes/web/web_downloads.py | line 123 orig 112 | current
`POST /web/downloads` no longer rotates the CSRF cookie after a successful HTMX submission, but existing tests (and the prior behavior) expect CSRF rotation on success. Re-introduce CSRF rotation before returning the fragment so the client receives a fresh `csrf_token` cookie.

====================================================================================================
ID 3487309050 | kilo-code-bot[bot] | app/services/yt_dlp_service.py | line None orig 478 | OUTDATED
**CRITICAL:** The fallback extraction loop is now nested inside `_progress_hook()`

Because this Python snippet is executed verbatim by `python -c`, moving the `for i, format_spec in enumerate(fallback_chain):` block under `_progress_hook()` leaves no top-level code that ever constructs `yt_dlp.YoutubeDL(...)`. The helper reaches the final `All formats failed` block immediately, so every extraction through this path can fail before a download even starts.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

====================================================================================================
ID 3487309052 | kilo-code-bot[bot] | app/auth.py | line 152 orig 128 | current
**WARNING:** `__Host-` cookies are rejected when `Secure` is false

These names require the `Secure` attribute in every browser. The new key names are used everywhere, but the call sites still pass `secure=settings.cookie_secure` and `cookie_secure` defaults to `False`, so local/default logins will silently fail to persist auth cookies.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

====================================================================================================
ID 3487309053 | kilo-code-bot[bot] | app/api/middleware/security_headers.py | line None orig 21 | OUTDATED
**WARNING:** The new nonce-based `script-src` blocks the slide deck controller

`/slides` still renders an inline `<script>` without a nonce, while this policy now only allows inline scripts that carry `nonce-{...}`. Once this header ships, the presentation's keyboard/click navigation code is blocked and the deck becomes non-interactive in the browser.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

====================================================================================================
ID 3487309054 | kilo-code-bot[bot] | worker/browser_executor.py | line None orig 219 | OUTDATED
**WARNING:** Wrapping `CircuitBreakerOpenError` here bypasses deferred-job handling

`worker.processor._handle_execution_result()` only routes raw `CircuitBreakerOpenError` through `_handle_circuit_open()`. Converting it into `BrowserExecutorError("circuit_open")` makes browser-downloader outages go through normal retry scheduling instead of the dedicated deferred-until-recovery path that the rest of the worker uses for open circuits.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

====================================================================================================
ID 3487314157 | coderabbitai[bot] | app/api/routes/sse.py | line 524 orig 457 | current
_🩺 Stability & Availability_ | _🟠 Major_ | _⚡ Quick win_

**The new SSE limit will lock out legitimate auto-reconnects.**

`EventSource` reconnects automatically on transient disconnects. At `5/minute`, a short deploy or flaky network can exhaust the bucket and leave the page without live status/progress updates for the rest of the window. This stream needs a much larger burst budget than normal GET routes, or an exemption entirely.

====================================================================================================
ID 3487314159 | coderabbitai[bot] | app/api/routes/web/web_auth.py | line 9 orig 9 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _⚡ Quick win_

**Avoid importing Starlette’s private `_TemplateResponse` type.**

Aliasing a private dependency symbol into public route annotations is brittle and can break on a routine Starlette upgrade. Please annotate these handlers with a public response type instead of depending on an internal class name.

====================================================================================================
ID 3487314161 | coderabbitai[bot] | app/api/routes/web/web_dashboard.py | line 4 orig 4 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _⚡ Quick win_

[analysis chain omitted]

**Use a public response type for these handler annotations.** `app/api/routes/web/web_dashboard.py:19, 35, 50` imports the private `starlette.templating._TemplateResponse`; switch the return annotations to a public type such as `HTMLResponse` instead.

====================================================================================================
ID 3487314162 | coderabbitai[bot] | app/api/routes/web/web_downloads.py | line 8 orig 8 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _⚡ Quick win_

**Avoid typing against Starlette’s private `_TemplateResponse`.**

Line 8 imports an internal symbol just for annotations. Prefer a public response type such as `HTMLResponse` or `Response` here so this file is not coupled to Starlette internals across upgrades.

====================================================================================================
ID 3487314163 | coderabbitai[bot] | app/api/routes/web/web_downloads.py | line 123 orig 112 | current
_🎯 Functional Correctness_ | _🟠 Major_ | _⚡ Quick win_

**Restore CSRF rotation on the HTMX create response.**

This path no longer sets a fresh `csrf_token` cookie. `tests/test_api/test_web_routes.py::test_create_download_htmx_returns_canonical_row_and_rotates_csrf` still expects rotation here, so the current response regresses that contract.

<details>
<summary>Proposed fix</summary>

```diff
     resp = templates.TemplateResponse(
         request, "partials/_download_item.html", get_template_context(request, job=job),
     )
+    rotate_csrf_token(resp)
     return resp
```
</details>

Re-add the `rotate_csrf_token` import at the top of the file as well.

====================================================================================================
ID 3487314164 | coderabbitai[bot] | app/api/routes/web/web_settings.py | line 7 orig 7 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _⚡ Quick win_

[analysis chain omitted]

**Use a public response type here.** `starlette.templating._TemplateResponse` is an internal Starlette class, so this annotation couples the route to a private API. Switch the return annotation to `Response` or `HTMLResponse` instead.

====================================================================================================
ID 3487314176 | coderabbitai[bot] | core/redis_client.py | line 73 orig 76 | current
_🩺 Stability & Availability_ | _🟡 Minor_ | _⚡ Quick win_

**`reset_redis_client()` drops a live client without closing it.**

This helper clears the singleton slot but never closes the existing async Redis client first. In repeated test/setup cycles that leaks the old connection pool. Either make reset delegate to `close_redis_client()` or require callers to await shutdown before resetting.

====================================================================================================
ID 3487314177 | coderabbitai[bot] | tests/test_api/test_web_routes.py | line 2927 orig 2927 | current
_🔒 Security & Privacy_ | _🟡 Minor_ | _⚡ Quick win_

**Assert the refresh cookie is cleared here too.**

Line 2924 only verifies `__Host-access_token`, but the success path clears both auth cookies. This test will miss a regression that leaves `__Host-refresh_token` behind.

<details>
<summary>Suggested test update</summary>

```diff
         assert (
             "__Host-access_token" not in response.cookies
             or response.cookies.get("__Host-access_token") == ""
         )
+        assert (
+            "__Host-refresh_token" not in response.cookies
+            or response.cookies.get("__Host-refresh_token") == ""
+        )
```
</details>

<details>
<summary>📝 Committable suggestion</summary>

> ‼️ **IMPORTANT**
> Carefully review the code before committing. Ensure that it accurately replaces the highlighted code, contains no missing lines, and has no issues with indentation. Thoroughly test & benchmark the code to ensure it meets the requirements.

```suggestion
        assert (
            "__Host-access_token" not in response.cookies
            or response.cookies.get("__Host-access_token") == ""
        )
        assert (
            "__Host-refresh_token" not in response.cookies
            or response.cookies.get("__Host-refresh_token") == ""
        )
```

</details>

====================================================================================================
ID 3694507732 | aikido-pr-checks[bot] | packages/browser-downloader/package.json | line 1 orig 1 | current
**CVE-2025-59288 in playwright** - high severity
Improper verification of cryptographic signature in Github: Playwright allows an unauthorized attacker to perform spoofing over an adjacent network.

<details><summary>Details</summary>

**Remediation** Aikido suggests bumping this package to version 1.55.1 to resolve this issue

<sub>Reply `@AikidoSec ignore: [REASON]` to ignore this issue.</sub>
<sub>[More info](https://app.aikido.dev/repositories/1694931/pull_requests/141/latest?groupId=11274)</sub>
</details>

====================================================================================================
ID 3694508823 | github-advanced-security[bot] | packages/browser-downloader/src/validate.js | line 195 orig 159 | current
## CodeQL / Uncontrolled data used in path expression

This path depends on a [user-provided value](1).

[Show more details](https://github.com/tomkabel/vooglaadija/security/code-scanning/118)

====================================================================================================
ID 3694508826 | github-advanced-security[bot] | packages/browser-downloader/src/streamlink-backend.js | line 409 orig 336 | current
## CodeQL / Network data written to file

Write to file system depends on [Untrusted data](1).

[Show more details](https://github.com/tomkabel/vooglaadija/security/code-scanning/119)

====================================================================================================
ID 3694528964 | coderabbitai[bot] | app/services/download_service.py | line 442 orig 399 | current
_🗄️ Data Integrity & Integration_ | _🟠 Major_ | _⚡ Quick win_

**Update only the outbox row that started the fast path.**

The update matches every pending row for `job_id`. After `enqueue_job()` succeeds, the relay or worker can process the original row and create a later pending retry event. This update can then mark that later event as `processed` without queueing it.

Select and retain the original `Outbox.id` before `enqueue_job()`. Update by that ID and `status == "pending"`. Add a regression test that inserts a second pending outbox row between queue delivery and the update.

====================================================================================================
ID 3694528970 | coderabbitai[bot] | app/services/outbox_service.py | line 54 orig 59 | current
_🗄️ Data Integrity & Integration_ | _🟠 Major_ | _🏗️ Heavy lift_

[analysis chain omitted]

**Recover duplicate outbox inserts inside a savepoint.**

`await db.flush()` leaves the outer `AsyncSession` inactive after an `IntegrityError`; `db.expunge(outbox_entry)` does not make the transaction committable. `DownloadService.create()` and `DownloadService.retry()` then fail at `commit()` instead of handling the concurrent insert idempotently. Wrap the insert and flush in `db.begin_nested()`, return `None` only after confirming the pending outbox row exists, and re-raise other integrity failures.

====================================================================================================
ID 3694528974 | coderabbitai[bot] | core/models/outbox.py | line 67 orig 67 | current
_🗄️ Data Integrity & Integration_ | _🟠 Major_ | _🏗️ Heavy lift_

[analysis chain omitted]

**Add the `uq_outbox_pending_job_id` migration.**

The model declares the partial unique index, but `alembic/versions` does not contain a migration for `uq_outbox_pending_job_id`. Existing deployed databases will miss this constraint unless a PostgreSQL and SQLite migration is added for the `WHERE status = 'pending'` DDL.

---

_🗄️ Data Integrity & Integration_ | _🟠 Major_ | _⚡ Quick win_

[analysis chain omitted]

**Wrap the outbox insert in a savepoint.**

`write_job_to_outbox` catches concurrent `flush()` failures and calls `db.expunge()`, but the exception still leaves the top-level transaction unreadable for the enclosing `DownloadService.create` path. Use `await db.begin_nested()` around the insert/flush and add a two-session concurrency test for the partial unique index.

====================================================================================================
ID 3694528976 | coderabbitai[bot] | packages/browser-downloader/Dockerfile | line 30 orig 30 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _⚡ Quick win_

[analysis chain omitted]

**Pin the build inputs for reproducible images.**

`pip3 install streamlink` resolves to the available streamlink version at build time. `pnpm install --prod` has no lockfile in the Docker build step because only `package.json` is copied. Pin `streamlink` to a fixed version, and add the root `pnpm-lock.yaml` into the Docker build so `pnpm install --prod` can run with `--frozen-lockfile`.

</details>

_Source: Linters/SAST tools_

====================================================================================================
ID 3694528982 | coderabbitai[bot] | packages/browser-downloader/src/server.js | line 29 orig 29 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _💤 Low value_

**Make the concurrency limit configurable.**

`PORT`, tier timeouts, and the request timeout read from the environment. `MAX_CONCURRENCY` is hardcoded at 2. Operators cannot tune throughput per deployment without a code change. `parseTimeout` already provides safe integer parsing for this purpose.

<details>
<summary>♻️ Proposed change</summary>

```diff
-const MAX_CONCURRENCY = 2;
+const MAX_CONCURRENCY = parseTimeout(process.env.BD_MAX_CONCURRENCY, 2);
```
</details>

<details>
<summary>📝 Committable suggestion</summary>

> ‼️ **IMPORTANT**
> Carefully review the code before committing. Ensure that it accurately replaces the highlighted code, contains no missing lines, and has no issues with indentation. Thoroughly test & benchmark the code to ensure it meets the requirements.

```suggestion
const PORT = Number(process.env.BD_PORT) || 3000;
const MAX_CONCURRENCY = parseTimeout(process.env.BD_MAX_CONCURRENCY, 2);
const BODY_LIMIT = '1mb';
// Per-request overall timeout. With max concurrency=2, two slow targets could
// otherwise block the service indefinitely. The timeout races the download and
// releases the semaphore slot when it fires (env BD_REQUEST_TIMEOUT_MS).
const DEFAULT_REQUEST_TIMEOUT_MS = 300_000;

const limiter = createSemaphore(MAX_CONCURRENCY);
```

</details>

====================================================================================================
ID 3694528986 | coderabbitai[bot] | packages/browser-downloader/src/server.js | line 81 orig 72 | current
_🚀 Performance & Scalability_ | _🔵 Trivial_ | _💤 Low value_

**Validation runs twice per request.**

`validateUrl` and `validateOutputDir` run here, and `download()` runs both again at `packages/browser-downloader/src/downloader.js` lines 52-53. Each request therefore performs two DNS resolutions and two `realpath` pairs. The return values are also discarded here, so the normalized URL and the resolved directory are recomputed downstream.

Keep the validation in `download()` as the security boundary, because `downloadStream` also re-validates derived URLs. Then reduce this block to a cheap pre-check, or pass the validated values into `download()` so it can skip the repeat work. The distinction matters for the 400-versus-502 mapping: this block produces the 400 client-error response, so any change must preserve that mapping.

====================================================================================================
ID 3694528988 | coderabbitai[bot] | packages/browser-downloader/src/server.js | line 128 orig 116 | current
_🩺 Stability & Availability_ | _🟠 Major_ | _🏗️ Heavy lift_

**The timeout frees the slot but does not stop the download.**

When the timeout wins the race, `finally` calls `release()`. The `download()` call continues to run, because nothing cancels it. The Chromium instance, the CDP session, and any `streamlink` subprocess stay alive until they finish on their own. A new request can then acquire the freed slot and launch another browser.

With repeated hangs, the number of live browsers exceeds `MAX_CONCURRENCY` without bound. This defeats the concurrency limit and can exhaust memory on the container.

`downloadStream` already accepts `opts.signal` (see `packages/browser-downloader/src/streamlink-backend.js` line 511 and the `runSpawn` call at line 523). Thread an `AbortSignal` from the server through `download()` into the tier functions and `downloadStream`, and abort it when the timer fires. At minimum, close the browser on timeout so the slot and the browser are released together.

</details>

====================================================================================================
ID 3694528993 | coderabbitai[bot] | packages/browser-downloader/src/streamlink-backend.js | line 260 orig 211 | current
_🚀 Performance & Scalability_ | _🟠 Major_ | _⚡ Quick win_

**Retry amplification in `fetchResWithRetry` drives both the runtime cost and the test timing risk.** `contextCandidates` produces variants that `fetchResOne` cannot apply, because `credentials` and `mode` are dropped, and the retry loop treats terminal failures such as `404`, SSRF rejection, and `size cap exceeded` as retryable. Every resource therefore runs up to 16 attempts with backoff to 8 s.
- `packages/browser-downloader/src/streamlink-backend.js#L133-L211`: remove the dead `init` object in `fetchResOne`, reduce `contextCandidates` to the header variants that actually differ, and throw immediately for non-retryable 4xx statuses, `validateUrl` rejections, and size-cap errors.
- `packages/browser-downloader/tests/streamlink-backend.test.js#L284-L315`: after the source fix, confirm the redirect-SSRF test and the size-cap test fail fast; until then raise `testTimeout` for these two tests so they do not exceed the 5 s default.

<details>
<summary>📍 Affects 2 files</summary>

- `packages/browser-downloader/src/streamlink-backend.js#L133-L211` (this comment)
- `packages/browser-downloader/tests/streamlink-backend.test.js#L284-L315`

</details>

====================================================================================================
ID 3694529000 | coderabbitai[bot] | packages/browser-downloader/src/streamlink-backend.js | line 410 orig 337 | current
_🩺 Stability & Availability_ | _🟠 Major_ | _⚡ Quick win_

**The size caps do not stop an unbounded body when `Content-Length` is absent.**

`fetchResOne` checks `Content-Length` against the cap, but that header is optional. For a chunked response, `cl` is `null` and the check is skipped. `fetchText` then calls `res.text()` and `fetchToFile` calls `res.arrayBuffer()`, so the whole body is materialized in memory before the cap is compared at Lines 322 and 333. A CDN that omits `Content-Length` can therefore exhaust worker memory, and this path runs once per segment.

Stream the body and abort as soon as the cap is exceeded.

<details>
<summary>🛡️ Proposed fix</summary>

```diff
+// Read a response body incrementally and abort once `cap` bytes are exceeded.
+async function readCapped(res, cap, kind) {
+  if (cap == null || !res.body) {
+    return Buffer.from(await res.arrayBuffer());
+  }
+  const chunks = [];
+  let total = 0;
+  for await (const chunk of res.body) {
+    total += chunk.length;
+    if (total > cap) {
+      await res.body.cancel?.();
+      throw new DownloaderError('network_error', `${kind} body exceeds size cap`);
+    }
+    chunks.push(Buffer.from(chunk));
+  }
+  return Buffer.concat(chunks);
+}
+
 async function fetchText(url, opts = {}) {
   const resourceKind = classifyResource(url);
   const cap = opts.bodyCap ?? maxBodyBytes(resourceKind);
   const res = await fetchResWithRetry(url, opts);
-  const text = await res.text();
-  if (cap != null && Buffer.byteLength(text) > cap) {
-    throw new DownloaderError('network_error', `${resourceKind} body exceeds size cap`);
-  }
-  return text;
+  return (await readCapped(res, cap, resourceKind)).toString('utf8');
 }
 
 async function fetchToFile(url, destPath, opts = {}) {
   const resourceKind = classifyResource(url);
   const cap = opts.bodyCap ?? maxBodyBytes(resourceKind);
   const res = await fetchResWithRetry(url, opts);
-  const buf = Buffer.from(await res.arrayBuffer());
-  if (cap != null && buf.length > cap) {
-    throw new DownloaderError('network_error', `${resourceKind} body exceeds size cap`);
-  }
+  const buf = await readCapped(res, cap, resourceKind);
   await writeFile(destPath, buf);
 }
```
</details>

A 256 MiB segment cap also means one segment can hold 256 MiB of heap. Streaming directly to disk with a running byte counter would bound memory further.

</details>

====================================================================================================
ID 3694529002 | coderabbitai[bot] | packages/browser-downloader/src/streamlink-backend.js | line 555 orig 465 | current
_🚀 Performance & Scalability_ | _🔵 Trivial_ | _⚡ Quick win_

**`validateUrl` runs a DNS lookup for every segment URL.**

The loop calls `await validateUrl(href, { lookup })` once per segment line. A playlist with a thousand segments performs a thousand sequential DNS resolutions before any download starts. Segments almost always share one host.

Cache the validation result per hostname inside this call, and keep re-validating only when the host changes.

====================================================================================================
ID 3694529007 | coderabbitai[bot] | packages/browser-downloader/src/tier1-cdp.js | line 50 orig 50 | current
_🔒 Security & Privacy_ | _🟠 Major_ | _🏗️ Heavy lift_

**Captured `cookie` and `authorization` headers reach the process command line.**

`AUTH_HEADER_NAMES` captures `cookie` and `authorization`. These values are session credentials. `interceptMedia` attaches them to the result as `authHeaders` (line 264). `downloader.js` forwards them to `downloadStream` (line 89), and `downloadStream` appends each one to the `streamlink` argument list as `--http-header name=value` (see `packages/browser-downloader/src/streamlink-backend.js` lines 521-526).

Process arguments are world-readable on Linux through `/proc/<pid>/cmdline`. Any other process in the container can read the captured session cookie or bearer token. Argument lists can also reach process listings and crash dumps.

Pass credential headers to `streamlink` through a file or environment-based mechanism instead of argv. If replaying credentials is not required for the target platforms, restrict `AUTH_HEADER_NAMES` to `referer` and `origin`. Also confirm that `authHeaders` never reaches a log line or the HTTP response body.

```shell
#!/bin/bash
# Check how authHeaders is consumed and whether it is ever logged.
rg -n -C4 'authHeaders' packages/browser-downloader/src
rg -n -C2 'http-header|console\.(log|error|warn)' packages/browser-downloader/src/streamlink-backend.js
```

====================================================================================================
ID 3694529010 | coderabbitai[bot] | packages/browser-downloader/src/tier1-cdp.js | line 185 orig 165 | current
_🚀 Performance & Scalability_ | _🟡 Minor_ | _⚡ Quick win_

**`requestHeaders` grows for every request and retains credentials.**

`onRequestWillBeSent` inserts an entry for each request that carries any of `AUTH_HEADER_NAMES`. Nearly every subresource carries `referer` and `origin`, so most requests insert an entry. Entries are read once at line 256 and are never deleted. `candidates` deletes its entry at line 217, so the two maps behave differently.

A page that issues thousands of requests during the interception window accumulates thousands of entries. Each entry can hold a `cookie` or `authorization` value, so the map also keeps credentials in memory longer than needed.

Delete the entry after you read it at line 256, and cap the map size with oldest-entry eviction, in the same way `tier2-dom.js` caps `__bd_blobs` with `BLOB_CAP`.

Also applies to: 198-209

====================================================================================================
ID 3694529011 | coderabbitai[bot] | packages/browser-downloader/src/tier1-cdp.js | line 256 orig 236 | current
_🩺 Stability & Availability_ | _🟠 Major_ | _🏗️ Heavy lift_

**The size caps run after the full body is already in memory.**

Both caps check the size after `Network.getResponseBody` has returned the entire body and the code has decoded it. At line 233 the manifest is already a full JS string. At line 274 the response is already a full `Buffer`, decoded from a base64 string that is itself resident in memory. With the default `bodyCap` of 500 MiB, a single response can hold roughly 1 GiB across the base64 string and the buffer before the check rejects it.

`tier2-dom.js` lines 163-167 already implement the correct order. It reads `content-length` and returns `tooLarge` before it materializes the body.

Use the `encodedDataLength` value from the `Network.responseReceived` event, or the response `Content-Length` header, to reject an oversized candidate before you call `Network.getResponseBody`. Keep the current checks as a backstop.

Separately, `body.length` on line 233 counts UTF-16 code units, not bytes. `MANIFEST_CAP` is documented as 8 MiB. Use `Buffer.byteLength(body)` so the cap measures bytes.

Also applies to: 271-278

====================================================================================================
ID 3694529014 | coderabbitai[bot] | packages/browser-downloader/src/tier2-dom.js | line 41 orig 34 | current
_📐 Maintainability & Code Quality_ | _🟠 Major_ | _⚡ Quick win_

**The blob cap logic exists twice and the tested copy never runs.**

`pushBlobUrl` and the in-page `record` function implement the same cap-and-evict behavior. `HOOK_SRC` does not call `pushBlobUrl`, because the function is serialized into the page and cannot reference Node module scope. `HOOK_SRC` also redeclares the cap as a local `const CAP = 64` instead of using `BLOB_CAP`.

`packages/browser-downloader/tests/tier2-dom.test.js` lines 36-53 test `pushBlobUrl` only. The copy that actually runs in the browser has no coverage. If one copy changes, the other does not, and the tests still pass.

Pass the cap into the page as an argument and derive the in-page logic from a single source, for example `page.evaluate(HOOK_SRC, BLOB_CAP)` with `HOOK_SRC` accepting the cap parameter. That removes the duplicated constant. Note that the guard and eviction body must still be inlined in the injected function.

Also applies to: 42-48

====================================================================================================
ID 3694529016 | coderabbitai[bot] | packages/browser-downloader/src/tier2-dom.js | line 103 orig 92 | current
_🎯 Functional Correctness_ | _🔵 Trivial_ | _💤 Low value_

**`tryClickPlay` returns after the first match and can click an unrelated button.**

Two behaviors are worth reconsidering.

The function returns as soon as a selector yields a handle, even when the click itself fails. Line 85 swallows the click error. If `video` matches but the click is intercepted by an overlay, no later selector is tried, and playback never starts. Continue to the next selector when the click rejects.

The `'button'` fallback matches the first button in the document. On a page with a consent banner or a share dialog, this clicks an unrelated control on a third-party site. Restrict the fallback to a play-related selector, for example `button[aria-label*="play" i]`.

====================================================================================================
ID 3694529025 | coderabbitai[bot] | packages/browser-downloader/src/tier2-dom.js | line 203 orig 173 | current
_🩺 Stability & Availability_ | _🟠 Major_ | _🏗️ Heavy lift_

**`Array.from(new Uint8Array(ab))` serializes the media body as a JSON number array.**

The in-page callback converts the whole ArrayBuffer to a plain array of numbers. Playwright returns the value over CDP as JSON. Each byte becomes a decimal number plus a separator, so the wire payload is several times the media size, and both the page and the Node process hold the expanded form at once.

With the default `bodyCap` of 500 MiB, a large blob produces a multi-gigabyte JSON transfer. The process can run out of memory before line 187 rejects it. The comment at lines 153-154 states the intent to avoid this, but the intent only holds when the `content-length` header is present and the cap comparison works.

Transfer the bytes in a compact form instead. Encode the buffer as base64 in the page and decode with `Buffer.from(b64, 'base64')` in Node, or read the blob in bounded chunks. Also reject on `ab.byteLength` before any conversion.

Also applies to: 186-195

====================================================================================================
ID 3694529027 | coderabbitai[bot] | packages/browser-downloader/tests/downloader.test.js | line 119 orig 119 | current
_📐 Maintainability & Code Quality_ | _🟡 Minor_ | _⚡ Quick win_

**Isolate the test from an inherited `BD_DOWNLOAD_TIMEOUT_MS`.**

This test asserts the default timeout of `120_000`. `download()` reads `process.env.BD_DOWNLOAD_TIMEOUT_MS` when `opts.downloadTimeout` is absent. If the environment already defines that variable, the assertion fails. This describe block also has no `afterEach` that restores `process.env`. Pass the default explicitly or delete the variable in `beforeEach`.

<details>
<summary>💚 Proposed fix to remove the environment dependency</summary>

```diff
   beforeEach(async () => {
     base = await mkdtemp(join(tmpdir(), 'bd-dl-'));
+    delete process.env.BD_DOWNLOAD_TIMEOUT_MS;
     for (const m of Object.values(mocks)) {
```
</details>

====================================================================================================
ID 3694529028 | coderabbitai[bot] | packages/browser-downloader/tests/errors.test.js | line 41 orig 41 | current
_🎯 Functional Correctness_ | _🟠 Major_ | _⚡ Quick win_

[analysis chain omitted]

**Classify timeout causes through the fetch error path.** Move cause handling outside the `err.code` branch. Add a regression test where a `TypeError` has an `ETIMEDOUT` cause and expects `TIMEOUT`.

====================================================================================================
ID 3694529029 | coderabbitai[bot] | packages/browser-downloader/tests/server.test.js | line 179 orig 179 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _⚡ Quick win_

**Fixed sleeps synchronize the server tests.** Three tests wait a fixed number of milliseconds for an asynchronous event instead of awaiting the event. On a loaded CI runner these waits can expire before the expected state is reached, so the tests are flaky.
- `packages/browser-downloader/tests/server.test.js#L169-L179`: replace the 30 ms sleep with a barrier that the `download` mock resolves after two invocations, as proposed in the per-site comment.
- `packages/browser-downloader/tests/server.test.js#L189-L221`: replace the 50 ms sleep on line 198 and the 30 ms sleep on line 214 with `await once(second, 'error')` and `await once(server, 'error')` so the assertions run after the error handler executes.

</details>

<details>
<summary>📍 Affects 1 file</summary>

- `packages/browser-downloader/tests/server.test.js#L169-L179` (this comment)
- `packages/browser-downloader/tests/server.test.js#L189-L221`

</details>

---

_📐 Maintainability & Code Quality_ | _🟡 Minor_ | _⚡ Quick win_

**Replace the fixed 30 ms sleep with a deterministic barrier.**

The test assumes both requests acquire a semaphore slot within 30 ms. On a loaded CI runner they may not, so the third request can acquire a free slot and return 200. Signal entry from inside the `download` mock and wait for two entries before you send the third request.

<details>
<summary>💚 Proposed fix to remove the timing dependency</summary>

```diff
     const gate = [];
-    mocks.download.mockImplementation(() => new Promise((resolve) => gate.push(resolve)));
+    const entered = [];
+    const bothEntered = new Promise((resolveBarrier) => {
+      mocks.download.mockImplementation(() => {
+        entered.push(true);
+        if (entered.length === 2) {
+          resolveBarrier();
+        }
+        return new Promise((resolve) => gate.push(resolve));
+      });
+    });
     const { server, port } = await start();
     const body = { url: 'https://example.com/v', output_dir: '/output' };
     const r1 = post(port, body);
     const r2 = post(port, body);
-    await new Promise((r) => setTimeout(r, 30)); // let both acquire
+    await bothEntered;
     const r3 = await post(port, body);
```
</details>

<details>
<summary>📝 Committable suggestion</summary>

> ‼️ **IMPORTANT**
> Carefully review the code before committing. Ensure that it accurately replaces the highlighted code, contains no missing lines, and has no issues with indentation. Thoroughly test & benchmark the code to ensure it meets the requirements.

```suggestion
  it('returns 503 when 2 downloads are in flight (semaphore max=2)', async () => {
    const gate = [];
    const entered = [];
    const bothEntered = new Promise((resolveBarrier) => {
      mocks.download.mockImplementation(() => {
        entered.push(true);
        if (entered.length === 2) {
          resolveBarrier();
        }
        return new Promise((resolve) => gate.push(resolve));
      });
    });
    const { server, port } = await start();
    const body = { url: 'https://example.com/v', output_dir: '/output' };
    const r1 = post(port, body);
    const r2 = post(port, body);
    await bothEntered;
    const r3 = await post(port, body);
    expect(r3.status).toBe(503);
    expect((await r3.json()).error).toBe('concurrency_limit');
```

</details>

</details>

====================================================================================================
ID 3694529033 | coderabbitai[bot] | packages/browser-downloader/tests/streamlink-backend.test.js | line 106 orig 106 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _⚡ Quick win_

**Move `vi.unstubAllGlobals()` into `afterEach`.**

Each test calls `vi.unstubAllGlobals()` as its last statement. If an assertion fails first, that line never runs and the stubbed `fetch` leaks into the following tests. A failure in one test then produces confusing failures in unrelated tests.

<details>
<summary>♻️ Proposed change</summary>

```diff
-import { describe, expect, it, vi } from 'vitest';
+import { afterEach, describe, expect, it, vi } from 'vitest';
+
+afterEach(() => {
+  vi.unstubAllGlobals();
+  vi.restoreAllMocks();
+});
```

Then delete the trailing `vi.unstubAllGlobals()` call from each test body.
</details>

<details>
<summary>📝 Committable suggestion</summary>

> ‼️ **IMPORTANT**
> Carefully review the code before committing. Ensure that it accurately replaces the highlighted code, contains no missing lines, and has no issues with indentation. Thoroughly test & benchmark the code to ensure it meets the requirements.

```suggestion
  it('fails with drm_detected on encrypted HLS (`#EXT-X-KEY` with SAMPLE-AES) without fetching segments', async () => {
    let calls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        calls += 1;
        return okRes({
          text: '`#EXTM3U`\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://key"\nseg0.ts\n',
        });
      }),
    );
    await expect(
      downloadManifestFallback('https://x/master.m3u8', '/tmp/out.mp4', { timeout: 1000 }),
    ).rejects.toMatchObject({ code: 'drm_detected' });
    expect(calls).toBe(1); // manifest only — no segment fetch
  });
```

</details>

====================================================================================================
ID 3694529037 | coderabbitai[bot] | packages/browser-downloader/tests/streamlink-backend.test.js | line 315 orig 315 | current
_📐 Maintainability & Code Quality_ | _🟡 Minor_ | _⚡ Quick win_

**These two rejection tests can exceed the default Vitest timeout.**

Both cases fail inside `fetchResWithRetry`. The redirect case raises a plain `Error` from `validateUrl`, and the size-cap case raises `DownloaderError('network_error')`. Neither is treated as terminal by the retry loop, so each runs `2 contexts × 4 attempts = 8` fetches with backoff of 500 ms, 1 s, and 2 s per context. Total sleep time is about 7 s, which is above the 5 s default `testTimeout`.

The root cause is the retry classification in `packages/browser-downloader/src/streamlink-backend.js`. After that is fixed, both tests fail fast. Until then, these tests are timing-dependent.

====================================================================================================
ID 3694529039 | coderabbitai[bot] | packages/browser-downloader/tests/tier1-cdp.test.js | line 46 orig 46 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _⚡ Quick win_

**Page fakes return `undefined` for unrecognized probes in both test files.** Both fakes dispatch on the source text of the function passed to `page.evaluate` and fall through to `undefined`. A rename of a marker such as `__bd_drm` then makes security assertions pass on a falsy value instead of on real detection.
- `packages/browser-downloader/tests/tier1-cdp.test.js#L34-L46`: replace the final `return undefined` in the `evaluate` fake with a throw that reports the unmatched source.
- `packages/browser-downloader/tests/tier2-dom.test.js#L5-L34`: replace the final `return undefined` on line 27 with the same throw, and keep the explicit `return undefined` for the `createObjectURL` hook patch.

<details>
<summary>📍 Affects 2 files</summary>

- `packages/browser-downloader/tests/tier1-cdp.test.js#L34-L46` (this comment)
- `packages/browser-downloader/tests/tier2-dom.test.js#L5-L34`

</details>

====================================================================================================
ID 3694529041 | coderabbitai[bot] | packages/browser-downloader/tests/validate.test.js | line 137 orig 137 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _⚡ Quick win_

**This test can pass for the wrong reason.**

The call omits `base` and `realpath`, so `validateOutputDir` uses `BD_OUTPUT_BASE` or `/output` and the real filesystem. `/output` normally does not exist in a test environment, so the rejection comes from the "output base does not exist" branch on line 155 of `packages/browser-downloader/src/validate.js`, not from the missing subdirectory. Pass the temporary directory as `base` and assert the specific message.

<details>
<summary>💚 Proposed fix</summary>

```diff
   it('rejects a non-existent directory', async () => {
     const base = await mkdtemp(join(tmpdir(), 'bd-out-'));
-    await expect(validateOutputDir(join(base, 'does-not-exist'))).rejects.toThrow();
+    await expect(validateOutputDir(join(base, 'does-not-exist'), { base })).rejects.toThrow(
+      /output_dir does not exist/,
+    );
   });
```
</details>

<details>
<summary>📝 Committable suggestion</summary>

> ‼️ **IMPORTANT**
> Carefully review the code before committing. Ensure that it accurately replaces the highlighted code, contains no missing lines, and has no issues with indentation. Thoroughly test & benchmark the code to ensure it meets the requirements.

```suggestion
  it('rejects a non-existent directory', async () => {
    const base = await mkdtemp(join(tmpdir(), 'bd-out-'));
    await expect(validateOutputDir(join(base, 'does-not-exist'), { base })).rejects.toThrow(
      /output_dir does not exist/,
    );
  });
```

</details>

====================================================================================================
ID 3694529044 | coderabbitai[bot] | tests/test_story_3_5_processor_retry_dlq_extraction.py | line 404 orig 404 | current
_🎯 Functional Correctness_ | _🟡 Minor_ | _⚡ Quick win_

**Assert the actual pending-status filter.**

The `_PENDING_STATUS = "pending"` declaration alone satisfies this assertion. The test would still pass if every relay query removed its pending-status predicate.

Require `Outbox.status == _PENDING_STATUS` or `Outbox.status == "pending"` in the relay source.

  

<details>
<summary>Proposed fix</summary>

```diff
-assert 'Outbox.status == "pending"' in source or '_PENDING_STATUS = "pending"' in source
+assert (
+    'Outbox.status == "pending"' in source
+    or "Outbox.status == _PENDING_STATUS" in source
+)
```
</details>

<details>
<summary>📝 Committable suggestion</summary>

> ‼️ **IMPORTANT**
> Carefully review the code before committing. Ensure that it accurately replaces the highlighted code, contains no missing lines, and has no issues with indentation. Thoroughly test & benchmark the code to ensure it meets the requirements.

```suggestion
    # The relay still filters on the pending status; we keep it as a constant.
    assert (
        'Outbox.status == "pending"' in source
        or "Outbox.status == _PENDING_STATUS" in source
    )
```

</details>

====================================================================================================
ID 3694529048 | coderabbitai[bot] | tests/test_story_5_6_model_index_consistency.py | line 118 orig 118 | current
_🗄️ Data Integrity & Integration_ | _🔵 Trivial_ | _⚡ Quick win_

[analysis chain omitted]

**Assert the outbox pending index contract, not only the name.**

Naming `uq_outbox_pending_job_id` does not prove the migration/SQL contract. Assert `index.unique` and the `status = 'pending'` predicate from the model metadata and SQL inspection path.

====================================================================================================
ID 3694529051 | coderabbitai[bot] | tests/test_worker/test_browser_executor.py | line 304 orig 296 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _💤 Low value_

**Rename the test to match its new expectation.**

The test now asserts `CircuitBreakerOpenError`, not a transient error, so `test_open_circuit_raises_transient_without_http_call` is misleading. Rename it to `test_open_circuit_propagates_circuit_breaker_open_without_http_call`.

Also move the `CircuitBreakerOpenError` import to the module top level, next to the other imports.

====================================================================================================
ID 3694529053 | coderabbitai[bot] | worker/browser_executor.py | line 361 orig 341 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _⚡ Quick win_

**Drop the `type: ignore` and use `NoReturn`.**

Line 337 annotates `code: str` while `payload.get("error")` can return `None` or a non-string, which forces the `type: ignore`. The function also never returns; it always raises. Both can be expressed accurately.

<details>
<summary>♻️ Proposed change</summary>

```diff
 def _parse_failure_payload(
     payload: dict[str, Any],
     *,
     fallback_code: str = "unknown_error",
-) -> tuple[str, str, str | None]:
+) -> NoReturn:
@@
-    code: str = payload.get("error")  # type: ignore[assignment]
-    if not isinstance(code, str) or not code:
-        code = fallback_code
+    raw_code = payload.get("error")
+    code = raw_code if isinstance(raw_code, str) and raw_code else fallback_code
     category = _map_response_to_category(code, payload)
     raise BrowserExecutorError(category=category, signal=code)
```

Import `NoReturn` from `typing`. Note that `_parse_success` at Line 280 uses `return _parse_failure_payload(payload)`; with `NoReturn` that line still type-checks, and you can simplify it to a bare call.
</details>

<details>
<summary>📝 Committable suggestion</summary>

> ‼️ **IMPORTANT**
> Carefully review the code before committing. Ensure that it accurately replaces the highlighted code, contains no missing lines, and has no issues with indentation. Thoroughly test & benchmark the code to ensure it meets the requirements.

```suggestion
def _parse_failure_payload(
    payload: dict[str, Any],
    *,
    fallback_code: str = "unknown_error",
) -> NoReturn:
    """Map a structured failure payload to an error category.

    Accepts both 200-with-failed-status and non-200 responses.
    Raises BrowserExecutorError so the circuit breaker records a failure.

    When the JSON body lacks an explicit ``error`` field, ``fallback_code``
    (typically the synthesized ``http_<status>`` signal) is used so HTTP
    404/403/429 still map to their correct categories.
    """
    raw_code = payload.get("error")
    code = raw_code if isinstance(raw_code, str) and raw_code else fallback_code
    category = _map_response_to_category(code, payload)
    raise BrowserExecutorError(category=category, signal=code)
```

</details>

====================================================================================================
ID 3694529055 | coderabbitai[bot] | worker/main.py | line 286 orig 273 | current
_🚀 Performance & Scalability_ | _🔵 Trivial_

**Validate the 2-second outbox polling interval under production load.**

The new default can call `sync_outbox_to_queue()` up to 15 times more often per worker. Verify that the pending-outbox query and database capacity support the configured worker count.

====================================================================================================
ID 3694529059 | coderabbitai[bot] | worker/outbox_relay.py | line 59 orig 59 | current
_🎯 Functional Correctness_ | _🟡 Minor_ | _⚡ Quick win_

[analysis chain omitted]

**Calculate pending age outside SQLite datetime arithmetic.**

`func.now() - func.min(Outbox.created_at)` does not produce a timestamp interval on SQLite, and the `except` path resets `OUTBOX_OLDEST_PENDING_SECONDS` to `0`. Fetch the oldest `created_at` and compute elapsed seconds in Python, or use dialect-specific datetime arithmetic.

====================================================================================================
ID 3694531544 | coderabbitai[bot] | packages/browser-downloader/tests/downloader.test.js | line 189 orig 189 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _⚡ Quick win_

[analysis chain omitted]

**Use Vitest environment stubs for `BD_DOWNLOAD_TIMEOUT_MS`.**

Set this fixture with `vi.stubEnv('BD_DOWNLOAD_TIMEOUT_MS', '60000')` and restore it in `afterEach` with `vi.unstubAllEnvs()` instead of storing/replacing `process.env`. This keeps environment changes scoped and uses Vitest’s supported restore API.

<details>
<summary>♻️ Proposed refactor</summary>

```diff
-  let env;
-
   beforeEach(async () => {
     base = await mkdtemp(join(tmpdir(), 'bd-dl-'));
-    env = { ...process.env };
```

```diff
-  afterEach(() => {
-    process.env = env;
-  });
+  afterEach(() => {
+    vi.unstubAllEnvs();
+  });
```

Then set the variable with `vi.stubEnv('BD_DOWNLOAD_TIMEOUT_MS', '60000')` on line 200.
</details>

====================================================================================================
ID 3694531548 | coderabbitai[bot] | packages/browser-downloader/tests/validate.test.js | line 132 orig 132 | current
_📐 Maintainability & Code Quality_ | _🔵 Trivial_ | _⚡ Quick win_

**This test does not exercise path traversal.**

The `realpath` fake replaces `` `${base}/../` `` with `/etc/`. The input is `/etc/passwd`, which does not contain that substring, so the fake behaves as the identity function. The test only proves that `/etc/passwd` is not under a temporary base. It does not cover a `..` segment or a symlink that resolves outside the base, which is the case the containment check in `validateOutputDir` defends against.

<details>
<summary>💚 Proposed test that models a symlink escape</summary>

```diff
   it('rejects a path outside the base (path traversal)', async () => {
     const base = await mkdtemp(join(tmpdir(), 'bd-out-'));
-    const realpath = async (p) => p.replace(`${base}/../`, '/etc/');
-    await expect(validateOutputDir('/etc/passwd', { base, realpath })).rejects.toThrow();
+    // `evil` looks like a child of the base but resolves outside it.
+    const inside = join(base, 'evil');
+    const realpath = async (p) => (p === inside ? '/etc' : p);
+    await expect(validateOutputDir(inside, { base, realpath })).rejects.toThrow(/under/);
+    // A `..` segment must also be rejected after resolution.
+    await expect(
+      validateOutputDir(join(base, '..', 'elsewhere'), { base, realpath: async (p) => p }),
+    ).rejects.toThrow(/under/);
   });
```
</details>

<details>
<summary>📝 Committable suggestion</summary>

> ‼️ **IMPORTANT**
> Carefully review the code before committing. Ensure that it accurately replaces the highlighted code, contains no missing lines, and has no issues with indentation. Thoroughly test & benchmark the code to ensure it meets the requirements.

```suggestion
  it('rejects a path outside the base (path traversal)', async () => {
    const base = await mkdtemp(join(tmpdir(), 'bd-out-'));
    // `evil` looks like a child of the base but resolves outside it.
    const inside = join(base, 'evil');
    const realpath = async (p) => (p === inside ? '/etc' : p);
    await expect(validateOutputDir(inside, { base, realpath })).rejects.toThrow(/under/);
    // A `..` segment must also be rejected after resolution.
    await expect(
      validateOutputDir(join(base, '..', 'elsewhere'), { base, realpath: async (p) => p }),
    ).rejects.toThrow(/under/);
  });
```

</details>
