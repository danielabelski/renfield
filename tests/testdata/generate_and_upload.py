#!/usr/bin/env python3
"""Generate 100 test documents and upload to Renfield knowledge base.

Usage:
    # With default URL (https://renfield.local):
    python3 tests/testdata/generate_and_upload.py

    # With custom URL:
    RENFIELD_URL=https://192.168.1.159 python3 tests/testdata/generate_and_upload.py

Note: TXT format is NOT supported by Renfield's Docling document processor.
      All text-based documents use MD format instead.
"""

import sys
import os

# Add tests/testdata to path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generators import generate
from uploader import upload_document, ensure_kb_exists
from docs_arbeit import DOCS as ARBEIT_DOCS
from docs_privat import DOCS as PRIVAT_DOCS
from docs_verein import DOCS as VEREIN_DOCS


def main():
    print("=" * 70)
    print("Renfield Testdata Generator — 100 Dokumente")
    print("=" * 70)

    # 1. Ensure KBs exist
    print("\n[1/3] Knowledge Bases anlegen/prüfen...")
    kb_arbeit = ensure_kb_exists("Arbeit", "Berufliche Dokumente, Verträge, Emails")
    kb_privat = ensure_kb_exists("Privat", "Private Dokumente, Rechnungen, Versicherungen")
    kb_verein = ensure_kb_exists("Vereinsarbeit", "TV Angermund 04 e.V. Vereinsdokumente")
    print(f"  Arbeit: KB {kb_arbeit}")
    print(f"  Privat: KB {kb_privat}")
    print(f"  Vereinsarbeit: KB {kb_verein}")

    # 2. Build upload list: (filename, title, content, kb_id)
    all_docs = []
    for fname, title, content in ARBEIT_DOCS:
        all_docs.append((fname, title, content, kb_arbeit))
    for fname, title, content in PRIVAT_DOCS:
        all_docs.append((fname, title, content, kb_privat))
    for fname, title, content in VEREIN_DOCS:
        all_docs.append((fname, title, content, kb_verein))

    print(f"\n[2/3] {len(all_docs)} Dokumente generieren und hochladen...")
    total = len(all_docs)
    ok = 0
    fail = 0

    for i, (fname, title, content, kb_id) in enumerate(all_docs, 1):
        print(f"  [{i:3d}/{total}] {fname:50s} ", end="", flush=True)

        try:
            path = generate(fname, title, content)
            result = upload_document(path, kb_id)
            if result:
                ok += 1
            else:
                fail += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            fail += 1

    # 3. Summary
    print(f"\n{'=' * 70}")
    print(f"[3/3] Ergebnis: {ok} OK, {fail} FEHLER von {total} Dokumenten")
    print(f"{'=' * 70}")

    if fail > 0:
        print(f"\n{fail} Dokumente fehlgeschlagen. Prüfe die Backend-Logs.")
        return False

    print(f"\nAlle {ok} Dokumente erfolgreich hochgeladen!")
    print("  Warte auf Embedding-Verarbeitung (kann einige Minuten dauern).")
    print("  Dann: python3 tests/testdata/verify_search.py")
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
