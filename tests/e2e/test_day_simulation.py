"""
Langzeit-Test: Ganztags-Simulation mit Screenrecording.

Simulates a full day of Renfield usage (~2h 15min) with Playwright video
recording. Tests all features: chat, weather, calendar, smart home, media,
presence, RAG, email, news, web search, and agent loop.

Target: https://renfield.local (production, self-signed certs)

Usage:
    python3 tests/e2e/test_day_simulation.py
    python3 tests/e2e/test_day_simulation.py --headed     # visible browser
    python3 tests/e2e/test_day_simulation.py --fast        # 5s waits (debug)
    python3 tests/e2e/test_day_simulation.py --from 15     # start at scenario #15
"""

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

# Ensure imports work when run from project root
sys.path.insert(0, str(Path(__file__).parent))

from scenarios import ALL_SCENARIOS, Scenario
from monitor import SystemMonitor, ScenarioResult

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://renfield.local"
RESULTS_DIR = Path(__file__).parent / "results"
SCREENSHOTS_DIR = RESULTS_DIR / "screenshots"

# Human-like typing: ~50ms per character
TYPING_DELAY_MS = 50

# Log setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("day_simulation")


# ---------------------------------------------------------------------------
# Response validation patterns
# ---------------------------------------------------------------------------

# Patterns that indicate a broken or degraded response
_ERROR_PATTERNS = [
    "Entschuldigung, ich konnte die Anfrage nicht",
    "Ich kann diese Anfrage leider nicht bearbeiten",
    "Ein Fehler ist aufgetreten",
]

_WARNING_PATTERNS = [
    ("[Aktionsergebnis", "Raw action metadata in response"),
    ("failed:", "Tool failure reported in response"),
    ("error:", "Error reported in response"),
]


def _validate_response(response_text: str, sent_message: str, prev_response: str) -> tuple[str | None, list[str]]:
    """
    Validate response quality.

    Returns (error_or_none, list_of_warnings).
    """
    warnings = []

    if not response_text.strip():
        return "Empty response", warnings

    # Stale/duplicate detection: exact match with previous assistant response
    if prev_response and response_text.strip() == prev_response.strip():
        return f"Stale response: identical to previous answer", warnings

    # Check warning patterns
    lower = response_text.lower()
    for pattern, description in _WARNING_PATTERNS:
        if pattern.lower() in lower:
            warnings.append(description)

    # Check soft error patterns (response arrived but LLM reported failure)
    for pattern in _ERROR_PATTERNS:
        if pattern.lower() in lower:
            warnings.append(f"LLM error response: '{pattern}'")

    return None, warnings


# ---------------------------------------------------------------------------
# Browser interaction helpers
# ---------------------------------------------------------------------------


def _wait_for_chat_ready(page: Page, timeout: int = 15_000):
    """Wait for the chat input to be visible and enabled."""
    page.wait_for_selector("#chat-input", state="visible", timeout=timeout)
    page.wait_for_selector(
        "#chat-input:not([disabled])", state="attached", timeout=timeout
    )


def _count_articles(page: Page) -> int:
    """Count current message article elements."""
    return len(page.locator("div[role='article']").all())


def _get_last_assistant_text(page: Page) -> str:
    """Extract text from the last assistant message bubble.

    Uses the very last article element and extracts text from the
    assistant bubble (gray background). Strips [Aktionsergebnis ...]
    prefix if present to get the actual natural-language response.
    """
    articles = page.locator("div[role='article']").all()
    if not articles:
        return ""

    # The last article after sending should be the assistant response
    last_article = articles[-1]

    # Try to get text from the message content area
    # Assistant bubbles use bg-gray-200 (light) / bg-gray-700 (dark)
    bubble = last_article.locator("div.bg-gray-200, div.dark\\:bg-gray-700").first
    if bubble.count() > 0:
        text = bubble.inner_text()
    else:
        # Fallback: get all text from the article
        text = last_article.inner_text()

    return text.strip()


def _extract_clean_response(raw_text: str) -> str:
    """Strip [Aktionsergebnis ...] prefix from response text."""
    if raw_text.startswith("[Aktionsergebnis"):
        bracket_end = raw_text.find("]\n\n")
        if bracket_end > 0:
            return raw_text[bracket_end + 3:].strip()
        # Closing bracket without double newline — try single
        bracket_end = raw_text.find("]\n")
        if bracket_end > 0:
            return raw_text[bracket_end + 2:].strip()
    return raw_text


def _has_agent_steps(page: Page) -> bool:
    """Check if the last message contains agent step details."""
    details = page.locator("details.mb-2.group")
    return details.count() > 0


def send_chat_message(
    page: Page,
    text: str,
    *,
    timeout_s: int = 120,
    typing_delay_ms: int = TYPING_DELAY_MS,
    prev_response: str = "",
) -> dict:
    """
    Type a message with human-like speed, send it, wait for response.

    Returns dict with response metrics including validation results.
    """
    _wait_for_chat_ready(page)

    # Capture current state BEFORE sending
    old_last_text = _get_last_assistant_text(page)
    initial_count = _count_articles(page)

    # Type with human-like delay
    chat_input = page.locator("#chat-input")
    chat_input.click()
    chat_input.type(text, delay=typing_delay_ms)

    # Press Enter to send
    start_time = time.time()
    chat_input.press("Enter")

    timeout_ms = timeout_s * 1000

    # 1. Wait for new articles to appear (user message + assistant start)
    try:
        page.wait_for_function(
            f"document.querySelectorAll('div[role=\"article\"]').length >= {initial_count + 2}",
            timeout=timeout_ms,
        )
    except Exception:
        elapsed = int((time.time() - start_time) * 1000)
        return {
            "response_time_ms": elapsed,
            "response_text": "",
            "error": f"Timeout: no response after {timeout_s}s",
            "warnings": [],
            "has_agent_steps": False,
        }

    # 2. Wait for streaming to complete: input becomes enabled again
    #    (disabled during streaming, re-enabled on 'done' message)
    try:
        page.wait_for_selector(
            "#chat-input:not([disabled])", state="attached", timeout=timeout_ms
        )
    except Exception:
        pass  # Fallback: continue anyway

    # 3. Also wait for any loading indicator to disappear
    try:
        page.locator("div[role='status']").wait_for(
            state="hidden", timeout=10_000
        )
    except Exception:
        pass

    # 4. Generous buffer for React DOM to settle after streaming
    page.wait_for_timeout(2500)

    elapsed = int((time.time() - start_time) * 1000)

    # 5. Read the NEW response (last article's text)
    raw_response = _get_last_assistant_text(page)
    clean_response = _extract_clean_response(raw_response)
    agent_steps = _has_agent_steps(page)

    # 6. Stale response detection: if response text hasn't changed,
    #    the test captured the PREVIOUS answer (timing/offset bug)
    error = None
    if raw_response and raw_response.strip() == old_last_text.strip():
        error = f"Stale response: text identical to previous assistant message"

    # 7. Validate response quality
    val_error, warnings = _validate_response(
        raw_response, text, prev_response
    )
    if val_error and not error:
        error = val_error

    return {
        "response_time_ms": elapsed,
        "response_text": clean_response[:500],
        "response_raw": raw_response[:500],
        "error": error,
        "warnings": warnings,
        "has_agent_steps": agent_steps,
    }


def take_screenshot(page: Page, scenario_id: int, label: str) -> str:
    """Take a screenshot and return the relative path."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    # Sanitize label for filename
    safe_label = re.sub(r"[^a-zA-Z0-9_-]", "_", label)[:40]
    filename = f"{scenario_id:03d}_{safe_label}.png"
    path = SCREENSHOTS_DIR / filename
    page.screenshot(path=str(path), full_page=True)
    return f"screenshots/{filename}"


def navigate_admin_pages(page: Page, paths: list[str]) -> list[str]:
    """Navigate through admin pages, take screenshots. Returns screenshot paths."""
    screenshots = []
    for path in paths:
        try:
            page.goto(f"{BASE_URL}{path}", wait_until="networkidle", timeout=15_000)
            page.wait_for_timeout(2000)  # Let page settle
            safe_name = path.replace("/", "_").strip("_")
            ss_path = take_screenshot(page, 38, f"admin_{safe_name}")
            screenshots.append(ss_path)
            logger.info("  Admin page %s — OK", path)
        except Exception as e:
            logger.warning("  Admin page %s — Error: %s", path, e)
    return screenshots


# ---------------------------------------------------------------------------
# Main simulation runner
# ---------------------------------------------------------------------------


def run_simulation(
    *,
    headed: bool = False,
    fast_mode: bool = False,
    start_from: int = 1,
):
    """Run the full day simulation with video recording."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    monitor = SystemMonitor(RESULTS_DIR)

    logger.info("=" * 70)
    logger.info("RENFIELD DAY SIMULATION — Langzeit-Test")
    logger.info("Target: %s", BASE_URL)
    logger.info("Headed: %s | Fast: %s | Start from: #%d", headed, fast_mode, start_from)
    logger.info("Results: %s", RESULTS_DIR)
    logger.info("=" * 70)

    # Track previous response for stale detection across scenarios
    prev_response = ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        context = browser.new_context(
            record_video_dir=str(RESULTS_DIR),
            record_video_size={"width": 1920, "height": 1080},
            ignore_https_errors=True,
            viewport={"width": 1920, "height": 1080},
            locale="de-DE",
        )
        page = context.new_page()
        page.set_default_timeout(30_000)

        # Start monitoring
        monitor.start()

        try:
            # Navigate to chat
            logger.info("Loading chat page...")
            page.goto(BASE_URL, wait_until="networkidle", timeout=30_000)
            _wait_for_chat_ready(page)
            take_screenshot(page, 0, "initial_chat")
            logger.info("Chat ready!")

            # Run scenarios
            for scenario in ALL_SCENARIOS:
                if scenario.id < start_from:
                    continue

                prev_response = _run_scenario(
                    page, scenario, monitor,
                    fast_mode=fast_mode,
                    prev_response=prev_response,
                )

        except KeyboardInterrupt:
            logger.info("Test interrupted by user (Ctrl+C)")
        except Exception as e:
            logger.error("Fatal error: %s", e, exc_info=True)
            take_screenshot(page, 999, "fatal_error")
        finally:
            # Save video
            logger.info("Closing browser, saving video...")
            page.close()
            context.close()
            browser.close()

            # Stop monitor and generate reports
            monitor.stop()

            bug_report = monitor.generate_bug_report()
            report_path = RESULTS_DIR / "bug_report.md"
            report_path.write_text(bug_report, encoding="utf-8")
            logger.info("Bug report written to %s", report_path)

            # Print summary
            _print_summary(monitor)


def _run_scenario(
    page: Page,
    scenario: Scenario,
    monitor: SystemMonitor,
    *,
    fast_mode: bool = False,
    prev_response: str = "",
) -> str:
    """Execute a single scenario. Returns response text for stale detection."""
    logger.info("")
    logger.info(
        "━━━ Scenario #%d [%s] %s ━━━",
        scenario.id,
        scenario.phase,
        scenario.time_label,
    )

    if scenario.nav_pages:
        # Navigation scenario (admin pages tour)
        logger.info("  Navigating admin pages...")
        start = time.time()
        screenshots = navigate_admin_pages(page, scenario.nav_pages)
        elapsed = int((time.time() - start) * 1000)

        result = ScenarioResult(
            id=scenario.id,
            message="Admin-Seiten Tour",
            feature=scenario.feature,
            phase=scenario.phase,
            response_time_ms=elapsed,
            response_text=f"Visited {len(scenario.nav_pages)} pages",
            screenshot=screenshots[0] if screenshots else None,
        )
        monitor.add_scenario_result(result)

        # Return to chat for any remaining scenarios
        page.goto(BASE_URL, wait_until="networkidle", timeout=15_000)
        _wait_for_chat_ready(page)
        return ""

    # Chat scenario
    logger.info("  Message: \"%s\"", scenario.message)
    logger.info("  Expected: %s (timeout: %ds)", scenario.feature, scenario.response_timeout_s)

    response = send_chat_message(
        page,
        scenario.message,
        timeout_s=scenario.response_timeout_s,
        prev_response=prev_response,
    )

    # Take screenshot
    label = scenario.message[:30] if scenario.message else scenario.feature
    ss_path = take_screenshot(page, scenario.id, label)

    # Log result
    if response["error"]:
        logger.warning(
            "  ✗ ERROR: %s (after %dms)",
            response["error"],
            response["response_time_ms"],
        )
    else:
        preview = response["response_text"][:80].replace("\n", " ")
        agent_tag = " [Agent]" if response["has_agent_steps"] else ""
        logger.info(
            "  ✓ OK in %dms%s: %s...",
            response["response_time_ms"],
            agent_tag,
            preview,
        )

    if response.get("warnings"):
        for w in response["warnings"]:
            logger.warning("  ⚠ WARNING: %s", w)

    # Record result
    result = ScenarioResult(
        id=scenario.id,
        message=scenario.message,
        feature=scenario.feature,
        phase=scenario.phase,
        response_time_ms=response["response_time_ms"],
        response_text=response["response_text"],
        error=response["error"],
        warnings=response.get("warnings", []),
        screenshot=ss_path,
        has_agent_steps=response["has_agent_steps"],
    )
    monitor.add_scenario_result(result)

    # Wait between scenarios
    wait_s = 5 if fast_mode else scenario.wait_after_s
    logger.info("  Waiting %ds before next scenario...", wait_s)
    page.wait_for_timeout(wait_s * 1000)

    # Return raw response for stale detection in next scenario
    return response.get("response_raw", response["response_text"])


def _print_summary(monitor: SystemMonitor):
    """Print a summary to the console."""
    total = len(monitor.scenario_results)
    errors = sum(1 for s in monitor.scenario_results if s.error)
    warns = sum(1 for s in monitor.scenario_results if s.warnings)
    ok = total - errors

    logger.info("")
    logger.info("=" * 70)
    logger.info("SIMULATION COMPLETE")
    logger.info("=" * 70)
    logger.info("  Scenarios: %d total, %d OK, %d errors, %d warnings", total, ok, errors, warns)

    if monitor.scenario_results:
        times = [s.response_time_ms for s in monitor.scenario_results if s.response_time_ms > 0]
        if times:
            logger.info(
                "  Response times: min=%dms, max=%dms, avg=%dms",
                min(times),
                max(times),
                sum(times) // len(times),
            )

    health_ok = sum(1 for s in monitor.health_samples if s.health == "ok")
    health_total = len(monitor.health_samples)
    logger.info("  Health checks: %d/%d OK", health_ok, health_total)

    if errors:
        logger.info("")
        logger.info("FAILED SCENARIOS:")
        for s in monitor.scenario_results:
            if s.error:
                logger.info("  #%d [%s] %s — %s", s.id, s.phase, s.message[:40], s.error)

    if warns:
        logger.info("")
        logger.info("SCENARIOS WITH WARNINGS:")
        for s in monitor.scenario_results:
            if s.warnings:
                logger.info("  #%d [%s] %s — %s", s.id, s.phase, s.message[:40], "; ".join(s.warnings))

    logger.info("")
    logger.info("Results: %s", RESULTS_DIR)
    logger.info("  Video:       results/*.webm")
    logger.info("  Screenshots: results/screenshots/")
    logger.info("  Bug report:  results/bug_report.md")
    logger.info("  Metrics:     results/system_metrics.json")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Renfield Day Simulation — 2h+ long-running test with video recording"
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run with visible browser window",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: 5s waits between scenarios (for debugging)",
    )
    parser.add_argument(
        "--from",
        dest="start_from",
        type=int,
        default=1,
        help="Start from scenario number (default: 1)",
    )
    args = parser.parse_args()

    run_simulation(
        headed=args.headed,
        fast_mode=args.fast,
        start_from=args.start_from,
    )


if __name__ == "__main__":
    main()
