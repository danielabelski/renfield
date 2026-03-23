"""Upload documents to Renfield knowledge base.

Usage:
    BASE_URL defaults to https://renfield.local.
    Override via environment variable RENFIELD_URL or by calling set_base_url().
"""

import os
import time
import requests
import warnings

warnings.filterwarnings("ignore")

BASE_URL = os.environ.get("RENFIELD_URL", "https://renfield.local")


def set_base_url(url: str):
    """Override the base URL at runtime."""
    global BASE_URL
    BASE_URL = url.rstrip("/")


def upload_document(filepath, kb_id, max_wait=180):
    """Upload a file and wait for processing to complete."""
    filename = filepath.name
    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{BASE_URL}/api/knowledge/upload",
            params={"knowledge_base_id": kb_id},
            files={"file": (filename, f)},
            verify=False,
            timeout=60,
        )

    if resp.status_code == 409:
        print("  SKIP (duplicate)")
        return True

    resp.raise_for_status()
    data = resp.json()
    doc_id = data.get("document_id") or data.get("id")

    if not doc_id:
        print("  OK (no doc_id in response)")
        return True

    # Poll for completion
    start = time.time()
    while time.time() - start < max_wait:
        time.sleep(3)
        try:
            status_resp = requests.get(
                f"{BASE_URL}/api/knowledge/documents/{doc_id}",
                verify=False,
                timeout=10,
            )
            if status_resp.status_code == 200:
                doc = status_resp.json()
                status = doc.get("status", "")
                if status == "completed":
                    chunks = doc.get("chunk_count", "?")
                    print(f"  OK ({chunks} chunks)")
                    return True
                elif status in ("failed", "error"):
                    print(f"  FAILED: {doc.get('error', 'unknown')}")
                    return False
        except Exception:
            pass

    print(f"  TIMEOUT after {max_wait}s")
    return False


def ensure_kb_exists(name, description=""):
    """Get or create a knowledge base by name, return its ID."""
    resp = requests.get(
        f"{BASE_URL}/api/knowledge/bases",
        verify=False,
        timeout=10,
    )
    resp.raise_for_status()
    for kb in resp.json():
        if kb["name"] == name:
            return kb["id"]

    # Create
    resp = requests.post(
        f"{BASE_URL}/api/knowledge/bases",
        json={"name": name, "description": description, "is_public": True},
        verify=False,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["id"]
