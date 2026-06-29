"""Tests for the satellite network watchdog script (Tier 1 reboot + Tier 2
backend-link restart).

The script is `provisioning/templates/renfield-net-watchdog.sh.j2` — pure bash
(no Jinja substitutions in the body), so we run it directly with stubbed
`ping`/`ss`/`systemctl`/`logger`/`ip` on PATH and assert on the side effects
(did it reboot? restart the service? how did the counters move?).

Tier 2 is the fix for the "satellite process up but wedged with no backend
connection, stayed dark for hours until power-cycled" failure: when the gateway
is reachable but the service has no ESTABLISHED link to the backend ingress for
N consecutive checks, restart the service (never reboot).
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.satellite

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "src/satellite/provisioning/templates/renfield-net-watchdog.sh.j2"
)

SVC = "renfield-satellite.service"
SAT_PID = "1068"
BACKEND_PORT = "443"
GW_THRESHOLD = "5"
MAX_REBOOTS = "3"
BACKEND_THRESHOLD = "10"
MAX_RESTARTS = "3"


def _write(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def env(tmp_path):
    """A sandbox: stub commands on PATH + temp state paths. Behaviour of the
    stubs is driven by env vars the test sets per-scenario."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "systemctl.calls"

    # ping: exit 0 (reachable) iff GATEWAY_UP=1
    _write(bindir / "ping", '#!/bin/bash\n[ "${GATEWAY_UP:-1}" = "1" ] && exit 0 || exit 1\n')
    # logger: no-op
    _write(bindir / "logger", "#!/bin/bash\nexit 0\n")
    # ip: print a default route so the gateway auto-derives if no target given
    _write(bindir / "ip", '#!/bin/bash\necho "default via 10.0.0.1 dev wlan0"\n')
    # ss: emit a line containing pid=<SAT_PID>, iff SS_HAS_LINK=1 (the satellite
    # holds an ESTABLISHED backend connection)
    _write(
        bindir / "ss",
        '#!/bin/bash\n'
        'if [ "${SS_HAS_LINK:-0}" = "1" ]; then\n'
        '  echo "ESTAB 0 0 192.168.1.225:50052 192.168.1.230:443 users:((\\"python\\",pid=${SAT_PID},fd=18))"\n'
        'fi\n'
        'exit 0\n',
    )
    # systemctl: record restart/reboot; answer is-active / show MainPID from env
    _write(
        bindir / "systemctl",
        '#!/bin/bash\n'
        f'echo "$@" >> "{calls}"\n'
        'case "$1" in\n'
        '  is-active) [ "${SVC_ACTIVE:-1}" = "1" ] && exit 0 || exit 3 ;;\n'
        '  show) echo "${SAT_PID}" ;;\n'  # -p MainPID --value
        '  restart|reboot) exit 0 ;;\n'
        'esac\n'
        'exit 0\n',
    )

    uptime = tmp_path / "uptime"
    uptime.write_text("9999.0 9000.0\n")  # well past the 10-min boot grace

    base = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "SAT_PID": SAT_PID,
        "RENFIELD_WD_STATE": str(tmp_path / "fails"),
        "RENFIELD_WD_BUDGET": str(tmp_path / "reboots"),
        "RENFIELD_WD_BSTATE": str(tmp_path / "bfails"),
        "RENFIELD_WD_BBUDGET": str(tmp_path / "restarts"),
        "RENFIELD_WD_UPTIME_FILE": str(uptime),
    }
    return {"tmp": tmp_path, "calls": calls, "base": base, "uptime": uptime}


def run(env, *, target="10.0.0.1", **overrides):
    e = {**env["base"], **{k: str(v) for k, v in overrides.items()}}
    args = [
        "bash", str(SCRIPT), target, GW_THRESHOLD, MAX_REBOOTS,
        SVC, BACKEND_PORT, BACKEND_THRESHOLD, MAX_RESTARTS,
    ]
    subprocess.run(args, env=e, check=True, capture_output=True, timeout=30)


def calls(env) -> str:
    return env["calls"].read_text() if env["calls"].exists() else ""


# --- Tier 2: backend-link restart ------------------------------------------

def test_connected_does_nothing_and_clears_counter(env):
    (env["tmp"] / "bfails").write_text("4")
    run(env, GATEWAY_UP=1, SVC_ACTIVE=1, SS_HAS_LINK=1)
    assert "restart" not in calls(env)
    assert "reboot" not in calls(env)
    assert not (env["tmp"] / "bfails").exists()  # cleared on a healthy link


def test_no_link_below_threshold_increments_only(env):
    run(env, GATEWAY_UP=1, SVC_ACTIVE=1, SS_HAS_LINK=0)
    assert "restart" not in calls(env)
    assert (env["tmp"] / "bfails").read_text().strip() == "1"


def test_no_link_at_threshold_restarts_service(env):
    (env["tmp"] / "bfails").write_text(str(int(BACKEND_THRESHOLD) - 1))  # 9 → 10
    run(env, GATEWAY_UP=1, SVC_ACTIVE=1, SS_HAS_LINK=0)
    assert f"restart {SVC}" in calls(env)
    assert "reboot" not in calls(env)  # NEVER reboot for a backend issue
    assert (env["tmp"] / "restarts").read_text().strip() == "1"  # budget spent


def test_restart_budget_exhausted_gives_up(env):
    (env["tmp"] / "bfails").write_text(str(int(BACKEND_THRESHOLD) - 1))
    (env["tmp"] / "restarts").write_text(MAX_RESTARTS)  # already at the cap
    run(env, GATEWAY_UP=1, SVC_ACTIVE=1, SS_HAS_LINK=0)
    assert "restart" not in calls(env)


def test_inactive_service_is_not_restarted(env):
    (env["tmp"] / "bfails").write_text(str(int(BACKEND_THRESHOLD) - 1))
    run(env, GATEWAY_UP=1, SVC_ACTIVE=0, SS_HAS_LINK=0)
    assert "restart" not in calls(env)
    assert not (env["tmp"] / "bfails").exists()  # counter reset when not running


# --- Tier 1 regression + grace ---------------------------------------------

def test_gateway_down_at_threshold_reboots(env):
    (env["tmp"] / "fails").write_text(str(int(GW_THRESHOLD) - 1))  # 4 → 5
    run(env, GATEWAY_UP=0)
    assert "reboot" in calls(env)


def test_gateway_down_below_threshold_no_reboot(env):
    run(env, GATEWAY_UP=0)
    assert "reboot" not in calls(env)
    assert (env["tmp"] / "fails").read_text().strip() == "1"


def test_boot_grace_suppresses_all_action(env):
    env["uptime"].write_text("120.0 60.0\n")  # < 600s grace
    (env["tmp"] / "bfails").write_text(str(int(BACKEND_THRESHOLD) - 1))
    run(env, GATEWAY_UP=1, SVC_ACTIVE=1, SS_HAS_LINK=0)
    assert calls(env) == ""  # neither tier acts during the boot grace
