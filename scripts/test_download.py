#!/usr/bin/env python3
"""
Integration test: register → login → create download → poll → download file.
Run against the live API server at http://localhost:8080.
Usage: python scripts/test_download.py
"""

import sys
import time

import httpx

BASE_URL = "http://localhost:8080/api/v1"
TEST_EMAIL = "autotest@example.com"
TEST_PASSWORD = "testpass123"
VIDEO_URL = "https://www.youtube.com/watch?v=WOrBSXcYZHk"
MAX_POLL_SECONDS = 180


def main():
    with httpx.Client(timeout=30.0) as client:
        # ── Step 1: Register ──────────────────────────────────────
        print("1. Registering test user...")
        resp = client.post(
            f"{BASE_URL}/auth/register",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
        if resp.status_code == 201:
            print(f"   ✓ Registered {TEST_EMAIL}")
        elif resp.status_code == 409:
            print("   ↳ User already exists, continuing...")
        else:
            print(f"   ✗ Registration failed ({resp.status_code}): {resp.text}")
            sys.exit(1)

        # ── Step 2: Login ─────────────────────────────────────────
        print("2. Logging in...")
        resp = client.post(
            f"{BASE_URL}/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
        if resp.status_code != 200:
            print(f"   ✗ Login failed ({resp.status_code}): {resp.text}")
            sys.exit(1)
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("   ✓ Logged in")

        # ── Step 3: Create download job ───────────────────────────
        print("3. Creating download job...")
        print(f"   URL: {VIDEO_URL}")
        resp = client.post(
            f"{BASE_URL}/downloads",
            json={"url": VIDEO_URL},
            headers=headers,
        )
        if resp.status_code != 201:
            print(f"   ✗ Job creation failed ({resp.status_code}): {resp.text}")
            sys.exit(1)
        job = resp.json()
        job_id = job["id"]
        print(f"   ✓ Job created: {job_id}")
        print(f"   Initial status: {job['status']}")

        # ── Step 4: Poll until completed ──────────────────────────
        print(f"4. Polling job status (max {MAX_POLL_SECONDS}s)...")
        last_status = ""
        for elapsed in range(1, MAX_POLL_SECONDS + 1):
            time.sleep(1)
            resp = client.get(f"{BASE_URL}/downloads/{job_id}", headers=headers)
            resp.raise_for_status()
            job = resp.json()
            status = job["status"]

            if status != last_status:
                print(f"   [{elapsed}s] Status changed: {last_status or '(start)'} → {status}")
                last_status = status

            if status == "completed":
                print(f"   ✓ Job completed after {elapsed}s")
                print(f"   File: {job.get('file_name', 'N/A')}")
                break
            elif status == "failed":
                print(f"   ✗ Job failed after {elapsed}s")
                print(f"   Error: {job.get('error', 'Unknown')}")
                sys.exit(1)
        else:
            print(f"   ✗ Timed out after {MAX_POLL_SECONDS}s (status: {status})")
            sys.exit(1)

        # ── Step 5: Download the file ─────────────────────────────
        print("5. Downloading file...")
        resp = client.get(
            f"{BASE_URL}/downloads/{job_id}/file",
            headers=headers,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            print(f"   ✗ Download failed ({resp.status_code}): {resp.text}")
            sys.exit(1)
        size = len(resp.content)
        cd = resp.headers.get("content-disposition", "N/A")
        ct = resp.headers.get("content-type", "N/A")
        print("   ✓ Downloaded!")
        print(f"   Size: {size:,} bytes ({size / 1024 / 1024:.1f} MB)")
        print(f"   Content-Type: {ct}")
        print(f"   Content-Disposition: {cd}")

    print("\n══════════════════════════════════════")
    print("  ALL STEPS PASSED")
    print("══════════════════════════════════════")


if __name__ == "__main__":
    main()
