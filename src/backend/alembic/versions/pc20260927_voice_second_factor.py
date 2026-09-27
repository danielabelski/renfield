"""voice second factor: per-user consent column

Revision ID: pc20260927_voice2fa
Revises: pc20260926_baseline
Create Date: 2026-09-27

Ein ECAPA-Stimmabdruck ist biometrisches Datum (Art. 9 DSGVO). Dass eine ANMELDUNG
ihn verlangt, ist eine Einwilligung je Person und keine Konfiguration — deshalb eine
Spalte auf `users` und nicht ein Flag in der ConfigMap. `speaker_id` taugte dafuer
nicht: die Verknuepfung entstand fuer die Sprecherkennung, nicht als Zustimmung zur
Anmeldung.

🛑 Standard `false` mit `server_default`, damit der Flag-off-Pfad byte-identisch
bleibt und ein bestehendes Konto die Huerde NICHT ungefragt bekommt.

🛑 Diese Migration MUSS vor dem Rollout laufen (`--migrate`): der ORM waehlt jede
Spalte, ein neuer Pod vor der Migration scheitert an jeder Abfrage auf `users` —
also am Anmelden.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "pc20260927_voice2fa"
down_revision: str | None = "pc20260926_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "users"
_COLUMN = "voice_second_factor_enabled"


def _has_column(conn) -> bool:
    return bool(conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
    """), {"t": _TABLE, "c": _COLUMN}).scalar())


def upgrade() -> None:
    conn = op.get_bind()
    # Idempotent: eine Neuinstallation hat die Spalte schon aus `create_all`
    # (s. `pc20260926_schema_baseline`), und dort darf diese Migration nicht
    # scheitern.
    if _has_column(conn):
        return
    op.add_column(_TABLE, sa.Column(
        _COLUMN, sa.Boolean(), nullable=False, server_default=sa.text("false"),
    ))


def downgrade() -> None:
    # Der Rueckweg wird durchlaufen, nicht behauptet: die Spalte verschwindet
    # wirklich. Wer sie zurueckbaut, verliert die Einwilligungen — das ist bei
    # einem Rueckbau gewollt, denn ohne die Spalte gibt es keinen zweiten Faktor,
    # der sie lesen koennte.
    conn = op.get_bind()
    if not _has_column(conn):
        return
    op.drop_column(_TABLE, _COLUMN)
