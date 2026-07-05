"""speaker FK ON DELETE actions (fix un-deletable speakers)

Revision ID: pc20260705_speaker_fk_ondelete
Revises: pc20260624_satellite_enrollment
Create Date: 2026-07-05

The three FKs referencing ``speakers.id`` were all created with the implicit
``ON DELETE NO ACTION``, so Postgres refused to delete ANY speaker that had ever
been used (it has embeddings, and may be referenced by conversations / a user
link). ``DELETE /api/speakers/{id}`` therefore 500'd on a foreign-key violation
for every real speaker — auto-enrolled "Unbekannter Sprecher" rows accumulated
with no way to remove or consolidate them.

Give each FK the semantically-correct delete action:

- ``speaker_embeddings.speaker_id`` → ``CASCADE`` (an embedding belongs to its
  speaker; delete it with the speaker). The delete/merge routes use bulk SQL, so
  this DB-level CASCADE does the work; the ORM keeps ``cascade="all,
  delete-orphan"`` (NOT ``passive_deletes`` — sqlite ignores DB-level ON DELETE)
  for any ORM ``db.delete(speaker)`` caller + the model tests.
- ``conversations.speaker_id`` → ``SET NULL`` (keep the conversation, drop the
  now-dangling speaker attribution).
- ``users.speaker_id`` → ``SET NULL`` (keep the user account, drop the link).

``merge_speakers`` explicitly REASSIGNS conversations + the user link to the
target before deleting the source, so a merge preserves attribution (the SET
NULL only fires on a plain delete).

Fully transactional. Chains off the real head ``pc20260624_satellite_enrollment``.
Prod has historically carried multiple alembic heads, so apply TARGETED
(``alembic upgrade pc20260705_speaker_fk_ondelete``), not ``upgrade head``.
"""
from alembic import op

revision = "pc20260705_speaker_fk_ondelete"
down_revision = "pc20260624_satellite_enrollment"
branch_labels = None
depends_on = None


# (constraint, table, referenced-col-owner, ondelete) for the three FKs.
_FKS = (
    ("speaker_embeddings_speaker_id_fkey", "speaker_embeddings", "CASCADE"),
    ("conversations_speaker_id_fkey", "conversations", "SET NULL"),
    ("users_speaker_id_fkey", "users", "SET NULL"),
)


def upgrade() -> None:
    for name, table, ondelete in _FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            name, table, "speakers", ["speaker_id"], ["id"], ondelete=ondelete
        )


def downgrade() -> None:
    # Revert to the original implicit NO ACTION (no ondelete).
    for name, table, _ondelete in reversed(_FKS):
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, "speakers", ["speaker_id"], ["id"])
