"""
Day simulation scenario definitions.

Each scenario defines a chat message (or navigation action) with timing,
expected feature, and wait duration. Used by test_day_simulation.py.
"""

from dataclasses import dataclass, field


@dataclass
class Scenario:
    """A single test scenario in the day simulation."""

    id: int
    phase: str
    time_label: str  # e.g. "0:05" — cosmetic only
    message: str  # Chat message to send (empty for navigation actions)
    feature: str  # Expected feature/intent category
    wait_after_s: int  # Seconds to wait after response before next scenario
    response_timeout_s: int = 120  # Max seconds to wait for response
    nav_pages: list[str] = field(default_factory=list)  # For navigation scenarios


# ---------------------------------------------------------------------------
# Phase 1: Morgen-Routine (0:00 - 0:25)
# ---------------------------------------------------------------------------
PHASE_1 = [
    Scenario(1, "Morgen", "0:00", "Guten Morgen!", "general.conversation", 30, 60),
    Scenario(2, "Morgen", "0:02", "Wie wird das Wetter heute?", "mcp.weather", 45, 90),
    Scenario(
        3,
        "Morgen",
        "0:05",
        "Was steht heute in meinem Kalender?",
        "mcp.calendar",
        45,
        90,
    ),
    Scenario(4, "Morgen", "0:08", "Zeige mir ungelesene Emails", "mcp.email", 60, 90),
    Scenario(
        5,
        "Morgen",
        "0:12",
        "Schalte das Licht im Arbeitszimmer ein",
        "mcp.homeassistant",
        30,
        60,
    ),
    Scenario(
        6, "Morgen", "0:14", "Was sind die aktuellen Nachrichten?", "mcp.news", 60, 90
    ),
    Scenario(
        7,
        "Morgen",
        "0:18",
        "Spiele Jazz Radio im Arbeitszimmer",
        "internal.play_radio",
        45,
        90,
    ),
    Scenario(
        8, "Morgen", "0:21", "Lautstärke auf 30", "internal.media_control", 30, 60
    ),
    Scenario(
        9,
        "Morgen",
        "0:23",
        "Wer ist gerade zuhause?",
        "internal.get_all_presence",
        30,
        60,
    ),
]

# ---------------------------------------------------------------------------
# Phase 2: Arbeits-Session (0:25 - 0:55)
# ---------------------------------------------------------------------------
PHASE_2 = [
    Scenario(
        10, "Arbeit", "0:25", "Stoppe die Musik", "internal.media_control", 20, 60
    ),
    Scenario(
        11,
        "Arbeit",
        "0:27",
        "Suche im Web nach Python asyncio best practices",
        "mcp.search",
        60,
        90,
    ),
    Scenario(
        12,
        "Arbeit",
        "0:32",
        "Was kostet ein Raspberry Pi 5 aktuell?",
        "mcp.search",
        60,
        90,
    ),
    Scenario(
        13,
        "Arbeit",
        "0:37",
        "Suche nach Rechnungen in meinen Dokumenten",
        "knowledge.search",
        60,
        90,
    ),
    Scenario(
        14,
        "Arbeit",
        "0:42",
        "Finde Dokumente von Stadtwerke",
        "knowledge.search",
        60,
        90,
    ),
    Scenario(
        15,
        "Arbeit",
        "0:47",
        "Nachrichten über künstliche Intelligenz",
        "mcp.news",
        60,
        90,
    ),
    Scenario(
        16,
        "Arbeit",
        "0:52",
        "Wie ist die Temperatur im Arbeitszimmer?",
        "mcp.homeassistant",
        45,
        60,
    ),
]

# ---------------------------------------------------------------------------
# Phase 3: Musik & Media (0:55 - 1:25)
# ---------------------------------------------------------------------------
PHASE_3 = [
    Scenario(
        17,
        "Media",
        "0:55",
        "Suche Musik von Dire Straits",
        "mcp.jellyfin",
        60,
        90,
    ),
    Scenario(
        18,
        "Media",
        "1:00",
        "Spiel das Album Brothers in Arms im Arbeitszimmer",
        "internal.play_album_on_dlna",
        60,
        120,
    ),
    Scenario(19, "Media", "1:05", "Nächster Track", "internal.media_control", 30, 60),
    Scenario(
        20, "Media", "1:07", "Welche Alben habe ich?", "mcp.jellyfin", 60, 90
    ),
    Scenario(21, "Media", "1:12", "Pause", "internal.media_control", 120, 60),
    Scenario(22, "Media", "1:16", "Weiter", "internal.media_control", 30, 60),
    Scenario(
        23, "Media", "1:18", "Stoppe die Musik", "internal.media_control", 20, 60
    ),
    Scenario(24, "Media", "1:20", "Suche nach 1Live", "mcp.radio", 60, 90),
    Scenario(
        25,
        "Media",
        "1:23",
        "Spiel 1Live im Arbeitszimmer",
        "internal.play_radio",
        60,
        90,
    ),
]

# ---------------------------------------------------------------------------
# Phase 4: Agent Loop & Komplexe Queries (1:25 - 1:50)
# ---------------------------------------------------------------------------
PHASE_4 = [
    Scenario(
        26, "Agent", "1:25", "Stoppe die Musik", "internal.media_control", 20, 60
    ),
    Scenario(
        27,
        "Agent",
        "1:27",
        "Suche Musik von Coldplay und spiele das Album Mylo Xyloto im Arbeitszimmer",
        "agent_loop",
        120,
        180,
    ),
    Scenario(
        28,
        "Agent",
        "1:35",
        "Wie wird das Wetter morgen und wenn es regnet, erinnere mich an einen Regenschirm",
        "agent_loop",
        120,
        180,
    ),
    Scenario(
        29,
        "Agent",
        "1:43",
        "Was steht morgen im Kalender und suche im Web nach dem Veranstaltungsort",
        "agent_loop",
        120,
        180,
    ),
]

# ---------------------------------------------------------------------------
# Phase 5: Abend-Routine & Smalltalk (1:50 - 2:15)
# ---------------------------------------------------------------------------
PHASE_5 = [
    Scenario(
        30, "Abend", "1:50", "Stoppe die Musik", "internal.media_control", 20, 60
    ),
    Scenario(
        31, "Abend", "1:52", "Erzähl mir einen Witz", "general.conversation", 45, 90
    ),
    Scenario(
        32, "Abend", "1:55", "Was weißt du über mich?", "memory", 45, 90
    ),
    Scenario(
        33,
        "Abend",
        "1:58",
        "Wie spät ist es in New York?",
        "general.conversation",
        30,
        60,
    ),
    Scenario(
        34,
        "Abend",
        "2:00",
        "Wettervorhersage für das Wochenende",
        "mcp.weather",
        45,
        90,
    ),
    Scenario(35, "Abend", "2:03", "Zeige meine Serien", "mcp.jellyfin", 60, 90),
    Scenario(
        36,
        "Abend",
        "2:07",
        "Schalte das Licht im Arbeitszimmer aus",
        "mcp.homeassistant",
        30,
        60,
    ),
    Scenario(37, "Abend", "2:10", "Gute Nacht!", "general.conversation", 30, 60),
]

# ---------------------------------------------------------------------------
# Phase 6: Admin-Seiten Tour (navigation only)
# ---------------------------------------------------------------------------
PHASE_6 = [
    Scenario(
        38,
        "Admin",
        "2:12",
        "",
        "ui_navigation",
        10,
        nav_pages=[
            "/rooms",
            "/knowledge",
            "/memory",
            "/knowledge-graph",
            "/admin/satellites",
            "/admin/integrations",
            "/admin/presence",
            "/admin/intents",
            "/admin/maintenance",
        ],
    ),
]

# ---------------------------------------------------------------------------
# All phases combined
# ---------------------------------------------------------------------------
ALL_SCENARIOS: list[Scenario] = (
    PHASE_1 + PHASE_2 + PHASE_3 + PHASE_4 + PHASE_5 + PHASE_6
)
