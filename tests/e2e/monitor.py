"""
System monitor — background health checks, docker stats, log scanning.

Runs in a daemon thread during the day simulation, collecting metrics
every POLL_INTERVAL seconds. Results are written to system_metrics.json.
"""

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import urllib3

# Suppress InsecureRequestWarning for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("day_simulation.monitor")

POLL_INTERVAL = 60  # seconds between health checks
BACKEND_URL = "http://localhost:8000"  # Direct API (avoids mDNS resolution issues)
DOCKER_CONTAINERS = ["renfield-backend", "renfield-postgres", "renfield-redis", "renfield-ollama"]


@dataclass
class HealthSample:
    timestamp: str
    health: str  # "ok" | "error" | "timeout"
    health_detail: dict = field(default_factory=dict)
    docker: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class ScenarioResult:
    id: int
    message: str
    feature: str
    phase: str
    response_time_ms: int
    response_text: str = ""
    ws_message_count: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    screenshot: str | None = None
    has_agent_steps: bool = False


class SystemMonitor:
    """Background monitor that collects health + docker metrics."""

    def __init__(self, results_dir: Path):
        self.results_dir = results_dir
        self.health_samples: list[HealthSample] = []
        self.scenario_results: list[ScenarioResult] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = None

    def start(self):
        """Start background monitoring thread."""
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("System monitor started (interval=%ds)", POLL_INTERVAL)

    def stop(self):
        """Stop monitoring and save final metrics."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._save_metrics()
        logger.info("System monitor stopped, metrics saved")

    def add_scenario_result(self, result: ScenarioResult):
        """Record a completed scenario result."""
        self.scenario_results.append(result)

    def _poll_loop(self):
        """Main polling loop (runs in background thread)."""
        import requests

        self._session = requests.Session()
        self._session.verify = False

        while not self._stop_event.is_set():
            sample = self._collect_sample()
            self.health_samples.append(sample)
            self._stop_event.wait(POLL_INTERVAL)

    def _collect_sample(self) -> HealthSample:
        """Collect a single health + docker stats sample."""
        ts = datetime.now(timezone.utc).isoformat()

        # Health check
        health = "error"
        health_detail = {}
        error = None
        try:
            resp = self._session.get(
                f"{BACKEND_URL}/health", timeout=10
            )
            if resp.status_code == 200:
                health = "ok"
                health_detail = resp.json()
            else:
                health = "error"
                error = f"HTTP {resp.status_code}"
        except Exception as e:
            health = "timeout"
            error = str(e)

        # Docker stats
        docker = self._collect_docker_stats()

        return HealthSample(
            timestamp=ts,
            health=health,
            health_detail=health_detail,
            docker=docker,
            error=error,
        )

    def _collect_docker_stats(self) -> dict:
        """Get CPU/memory for monitored containers via docker stats."""
        stats = {}
        try:
            result = subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    '{"name":"{{.Name}}","cpu":"{{.CPUPerc}}","mem":"{{.MemUsage}}","mem_perc":"{{.MemPerc}}"}',
                ]
                + DOCKER_CONTAINERS,
                capture_output=True,
                text=True,
                timeout=15,
            )
            for line in result.stdout.strip().splitlines():
                try:
                    entry = json.loads(line)
                    name = entry["name"].replace("renfield-", "")
                    stats[name] = {
                        "cpu": entry["cpu"],
                        "mem": entry["mem"],
                        "mem_perc": entry["mem_perc"],
                    }
                except (json.JSONDecodeError, KeyError):
                    continue
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("Docker stats failed: %s", e)
        return stats

    def _save_metrics(self):
        """Write collected metrics to JSON file."""
        self.results_dir.mkdir(parents=True, exist_ok=True)
        output = {
            "test_date": datetime.now(timezone.utc).isoformat(),
            "total_scenarios": len(self.scenario_results),
            "successful": sum(1 for s in self.scenario_results if not s.error),
            "failed": sum(1 for s in self.scenario_results if s.error),
            "with_warnings": sum(1 for s in self.scenario_results if s.warnings),
            "health_samples": [vars(s) for s in self.health_samples],
            "scenarios": [vars(s) for s in self.scenario_results],
        }
        metrics_path = self.results_dir / "system_metrics.json"
        metrics_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
        logger.info("Metrics written to %s", metrics_path)

    def generate_bug_report(self) -> str:
        """Generate a markdown bug report from collected results."""
        total = len(self.scenario_results)
        errors = [s for s in self.scenario_results if s.error]
        warnings_list = [s for s in self.scenario_results if s.warnings]
        successes = total - len(errors)

        # Health warnings
        health_issues = [s for s in self.health_samples if s.health != "ok"]

        lines = [
            f"# Langzeit-Test Bug Report — {datetime.now().strftime('%Y-%m-%d')}",
            "",
            "## Zusammenfassung",
            f"- **Testdauer:** {self._calc_duration()}",
            f"- **Szenarien:** {total}",
            f"- **Erfolge:** {successes}",
            f"- **Fehler:** {len(errors)}",
            f"- **Warnungen:** {len(warnings_list)}",
            f"- **Health-Warnungen:** {len(health_issues)}",
            "",
        ]

        if errors:
            lines.append("## Bugs")
            lines.append("")
            for i, s in enumerate(errors, 1):
                lines.extend([
                    f"### BUG-{i:03d}: Fehler in Szenario #{s.id}",
                    f"- **Phase:** {s.phase}",
                    f"- **Nachricht:** \"{s.message}\"",
                    f"- **Feature:** {s.feature}",
                    f"- **Symptom:** {s.error}",
                    f"- **Response-Zeit:** {s.response_time_ms}ms",
                ])
                if s.screenshot:
                    lines.append(f"- **Screenshot:** {s.screenshot}")
                lines.append("")

        if warnings_list:
            lines.append("## Warnungen")
            lines.append("")
            for s in warnings_list:
                for w in s.warnings:
                    lines.append(
                        f"- **Szenario #{s.id}** [{s.phase}] \"{s.message[:40]}\": {w}"
                    )
            lines.append("")

        if health_issues:
            lines.append("## Health-Warnungen")
            lines.append("")
            for h in health_issues:
                lines.append(
                    f"- **{h.timestamp}**: {h.health} — {h.error or 'no detail'}"
                )
            lines.append("")

        if not errors and not warnings_list and not health_issues:
            lines.append("Keine Bugs gefunden! Alle Szenarien erfolgreich.")
            lines.append("")

        # Performance summary
        if self.scenario_results:
            times = [s.response_time_ms for s in self.scenario_results if s.response_time_ms > 0]
            if times:
                lines.extend([
                    "## Performance",
                    "",
                    f"- **Schnellste Antwort:** {min(times)}ms",
                    f"- **Langsamste Antwort:** {max(times)}ms",
                    f"- **Durchschnitt:** {sum(times) // len(times)}ms",
                    "",
                ])

        return "\n".join(lines)

    def _calc_duration(self) -> str:
        """Calculate test duration from first to last scenario."""
        if len(self.scenario_results) < 2:
            return "N/A"
        # Approximate from scenario timing
        total_ms = sum(s.response_time_ms for s in self.scenario_results)
        total_s = total_ms // 1000
        h, remainder = divmod(total_s, 3600)
        m, s = divmod(remainder, 60)
        return f"{h}h {m}min {s}s (active response time)"
