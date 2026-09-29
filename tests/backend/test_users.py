"""
Tests für Users API

Testet:
- User CRUD Operations
- Password Reset
- Speaker Linking
- Permission-basierte Zugriffskontrolle
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.database import Role, Speaker, User
from services.voice_factor_preconditions import (
    NO_EMBEDDINGS,
    NO_PROFILE,
    PATH_OFF,
    RECOGNITION_OFF,
)

# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_auth_user(test_user, test_role):
    """Mock authenticated user with admin permissions"""
    test_user.role = test_role
    return test_user


@pytest.fixture
def mock_require_permission():
    """Mock permission requirement to allow access"""
    async def _mock_permission(permission):
        async def checker():
            return MagicMock(
                id=1,
                username="admin",
                role=MagicMock(permissions=["admin", "users.view", "users.manage"])
            )
        return checker
    return _mock_permission


# ============================================================================
# Model Tests
# ============================================================================

class TestUserModel:
    """Tests für das User Model"""

    @pytest.mark.database
    async def test_create_user(self, db_session: AsyncSession, test_role: Role):
        """Testet das Erstellen eines Users"""
        user = User(
            username="newuser",
            email="newuser@example.com",
            password_hash="hashedpassword",
            role_id=test_role.id,
            is_active=True
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        assert user.id is not None
        assert user.username == "newuser"
        assert user.email == "newuser@example.com"

    @pytest.mark.database
    async def test_user_unique_username(self, db_session: AsyncSession, test_user: User, test_role: Role):
        """Testet, dass Username eindeutig sein muss"""
        from sqlalchemy.exc import IntegrityError

        duplicate = User(
            username=test_user.username,
            password_hash="hash",
            role_id=test_role.id
        )
        db_session.add(duplicate)

        with pytest.raises(IntegrityError):
            await db_session.commit()

    @pytest.mark.database
    async def test_user_role_relationship(self, db_session: AsyncSession, test_user: User):
        """Testet die Beziehung zwischen User und Role"""
        result = await db_session.execute(
            select(User)
            .where(User.id == test_user.id)
            .options(selectinload(User.role))
        )
        user = result.scalar_one()

        assert user.role is not None
        assert user.role.name == "TestRole"

    @pytest.mark.database
    async def test_user_speaker_relationship(
        self,
        db_session: AsyncSession,
        test_user: User,
        test_speaker: Speaker
    ):
        """Testet die Beziehung zwischen User und Speaker"""
        test_user.speaker_id = test_speaker.id
        await db_session.commit()
        await db_session.refresh(test_user)

        result = await db_session.execute(
            select(User)
            .where(User.id == test_user.id)
            .options(selectinload(User.speaker))
        )
        user = result.scalar_one()

        assert user.speaker is not None
        assert user.speaker.name == test_speaker.name


# ============================================================================
# CRUD API Tests
# ============================================================================

class TestUserCRUDAPI:
    """Tests für User CRUD API (require mocked auth)"""

    @pytest.mark.integration
    async def test_list_users(self, async_client: AsyncClient, test_user: User):
        """Testet GET /api/users"""
        with patch('api.routes.users.require_permission') as mock_perm:
            mock_perm.return_value = lambda: test_user

            response = await async_client.get("/api/users")

        # Without proper auth mocking, expect 401 or the actual response
        assert response.status_code in [200, 401, 403]

    @pytest.mark.integration
    async def test_create_user_endpoint(
        self,
        async_client: AsyncClient,
        test_role: Role
    ):
        """Testet POST /api/users"""
        with patch('api.routes.users.require_permission') as mock_perm:
            mock_perm.return_value = lambda: MagicMock()

            response = await async_client.post(
                "/api/users",
                json={
                    "username": "apiuser",
                    "password": "SecurePass123!",
                    "email": "apiuser@example.com",
                    "role_id": test_role.id,
                    "is_active": True
                }
            )

        # Without proper auth mocking, expect 401 or the actual response
        assert response.status_code in [200, 201, 401, 403]

    @pytest.mark.integration
    async def test_get_nonexistent_user(self, async_client: AsyncClient):
        """Testet GET für nicht-existenten User"""
        response = await async_client.get("/api/users/99999")

        # Expect 404 or 401/403 if auth required
        assert response.status_code in [404, 401, 403]


class TestAdminUnlock:
    """POST /api/users/{id}/unlock (BL-0125) — the route functions are exercised
    directly (the permission dependency is resolved by FastAPI at request time
    and is covered by the auth-service tests); what matters here is the wiring
    to the lockout store, the 404 and the audit log."""

    @pytest.mark.database
    async def test_unlock_clears_lockout_and_reports_count(self, db_session: AsyncSession, test_user: User):
        from api.routes import users as users_routes
        from services.voice_factor_preconditions import voice_factor_lock_id

        with patch.object(users_routes.login_lockout, "unlock", new=AsyncMock(return_value=3)) as unlock:
            body = await users_routes.unlock_user(
                user_id=test_user.id, db=db_session, current_user=MagicMock(username="admin")
            )
        # 🛑 ZWEI Zaehler, nicht einer. Der zweite Faktor sperrt unter
        # `voice2fa:<id>`, damit ein Stimm-Fehlversuch den Passwortpfad nicht
        # mitsperrt — aber ein Entsperr-Knopf, der nur einen raeumt, meldet
        # „entsperrt" und laesst die Person draussen. Hier stand vorher
        # `assert_awaited_once_with(username)`; das war die Absicht, solange es
        # nur einen Zaehler gab.
        assert [c.args for c in unlock.await_args_list] == [
            (test_user.username,), (voice_factor_lock_id(test_user.id),),
        ]
        assert body["cleared_keys"] == 6, "die Summe beider Zaehler"
        assert test_user.username in body["message"]

    @pytest.mark.database
    async def test_unlock_reports_503_when_store_unreachable(self, db_session: AsyncSession, test_user: User):
        """Never a false 'cleared': with Redis down the lock may still stand."""
        from fastapi import HTTPException

        from api.routes import users as users_routes
        from services.login_lockout import LockoutStoreUnavailable

        with patch.object(
            users_routes.login_lockout, "unlock", new=AsyncMock(side_effect=LockoutStoreUnavailable("down"))
        ):
            with pytest.raises(HTTPException) as exc:
                await users_routes.unlock_user(
                    user_id=test_user.id, db=db_session, current_user=MagicMock(username="admin")
                )
        assert exc.value.status_code == 503

    @pytest.mark.database
    async def test_unlock_unknown_user_is_404(self, db_session: AsyncSession):
        from fastapi import HTTPException

        from api.routes import users as users_routes

        with patch.object(users_routes.login_lockout, "unlock", new=AsyncMock(return_value=0)) as unlock:
            with pytest.raises(HTTPException) as exc:
                await users_routes.unlock_user(
                    user_id=99999, db=db_session, current_user=MagicMock(username="admin")
                )
        assert exc.value.status_code == 404
        unlock.assert_not_awaited()

    @pytest.mark.database
    async def test_unlock_gate_denies_a_viewer_and_admits_a_manager(self, db_session: AsyncSession, monkeypatch):
        """The route's dependency is require_permission(USERS_MANAGE): with auth
        on, a users.view-only principal is refused (403), a users.manage one
        passes. Exercised on the very checker the route declares."""
        from fastapi import HTTPException

        from models.permissions import Permission
        from services import auth_service
        from services.auth_service import require_permission

        monkeypatch.setattr(auth_service.settings, "auth_enabled", True)
        checker = require_permission(Permission.USERS_MANAGE)

        viewer = MagicMock(id=41, has_permission=lambda p: p == Permission.USERS_VIEW.value)
        with pytest.raises(HTTPException) as exc:
            await checker(user=viewer, db=db_session)
        assert exc.value.status_code == 403

        manager = MagicMock(id=42, has_permission=lambda p: p == Permission.USERS_MANAGE.value)
        assert await checker(user=manager, db=db_session) is manager

    @pytest.mark.database
    async def test_list_marks_locked_users(self, db_session: AsyncSession, test_user: User):
        """`locked_out` comes from ONE scan of the lockout store, matched on the
        normalized username, and is False for everyone when nothing is held."""
        from api.routes import users as users_routes

        locked = {test_user.username.strip().lower()}
        with patch.object(users_routes.login_lockout, "locked_usernames", new=AsyncMock(return_value=locked)):
            page = await users_routes.list_users(db=db_session, current_user=MagicMock())
        by_name = {u.username: u for u in page.users}
        assert by_name[test_user.username].locked_out is True

        with patch.object(users_routes.login_lockout, "locked_usernames", new=AsyncMock(return_value=set())):
            page = await users_routes.list_users(db=db_session, current_user=MagicMock())
        assert all(u.locked_out is False for u in page.users)


# ============================================================================
# Query Tests
# ============================================================================

class TestUserQueries:
    """Tests für User-Abfragen"""

    @pytest.mark.database
    async def test_filter_by_role(
        self,
        db_session: AsyncSession,
        test_user: User,
        test_role: Role
    ):
        """Testet Filterung nach Rolle"""
        result = await db_session.execute(
            select(User).where(User.role_id == test_role.id)
        )
        users = result.scalars().all()

        assert len(users) >= 1
        assert all(u.role_id == test_role.id for u in users)

    @pytest.mark.database
    async def test_filter_by_active_status(
        self,
        db_session: AsyncSession,
        test_user: User
    ):
        """Testet Filterung nach Aktivstatus"""
        result = await db_session.execute(
            select(User).where(User.is_active)
        )
        users = result.scalars().all()

        assert len(users) >= 1
        assert all(u.is_active for u in users)

    @pytest.mark.database
    async def test_search_by_username(
        self,
        db_session: AsyncSession,
        test_user: User
    ):
        """Testet Suche nach Username"""
        result = await db_session.execute(
            select(User).where(User.username.ilike(f"%{test_user.username[:3]}%"))
        )
        users = result.scalars().all()

        assert len(users) >= 1


# ============================================================================
# Password Reset Tests
# ============================================================================

class TestPasswordReset:
    """Tests für Password Reset"""

    @pytest.mark.database
    async def test_update_password_hash(
        self,
        db_session: AsyncSession,
        test_user: User
    ):
        """Testet Aktualisierung des Passwort-Hash"""
        old_hash = test_user.password_hash
        new_hash = "newhash123456"

        test_user.password_hash = new_hash
        await db_session.commit()
        await db_session.refresh(test_user)

        assert test_user.password_hash == new_hash
        assert test_user.password_hash != old_hash


# ============================================================================
# Speaker Linking Tests
# ============================================================================

class TestSpeakerLinking:
    """Tests für Speaker-User Verknüpfung"""

    @pytest.mark.database
    async def test_link_speaker_to_user(
        self,
        db_session: AsyncSession,
        test_user: User,
        test_speaker: Speaker
    ):
        """Testet Verknüpfung von Speaker zu User"""
        test_user.speaker_id = test_speaker.id
        await db_session.commit()
        await db_session.refresh(test_user)

        assert test_user.speaker_id == test_speaker.id

    @pytest.mark.database
    async def test_unlink_speaker_from_user(
        self,
        db_session: AsyncSession,
        test_user: User,
        test_speaker: Speaker
    ):
        """Testet Aufheben der Speaker-Verknüpfung"""
        # First link
        test_user.speaker_id = test_speaker.id
        await db_session.commit()

        # Then unlink
        test_user.speaker_id = None
        await db_session.commit()
        await db_session.refresh(test_user)

        assert test_user.speaker_id is None

    @pytest.mark.database
    async def test_speaker_unique_link(
        self,
        db_session: AsyncSession,
        test_role: Role,
        test_speaker: Speaker
    ):
        """Testet, dass ein Speaker nur einem User zugewiesen werden kann"""
        # Create first user with speaker
        user1 = User(
            username="user1_speaker",
            password_hash="hash1",
            role_id=test_role.id,
            speaker_id=test_speaker.id
        )
        db_session.add(user1)
        await db_session.commit()

        # Try to create second user with same speaker
        user2 = User(
            username="user2_speaker",
            password_hash="hash2",
            role_id=test_role.id,
            speaker_id=test_speaker.id
        )
        db_session.add(user2)

        # Should fail due to unique constraint
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            await db_session.commit()


class TestDeviceAccountFlag:
    """`users.is_device_account` (auth-on cutover D-4b) must be settable through
    the admin API — the satellite gates read the flag, and without a route the
    only way to arm the feature would be a hand-written UPDATE against the
    production database. Route functions are called directly, like the unlock
    tests above."""

    @pytest.mark.database
    async def test_create_can_mint_a_device_account(
        self, db_session: AsyncSession, test_role: Role
    ):
        from api.routes import users as users_routes

        body = await users_routes.create_user(
            request=users_routes.CreateUserRequest(
                username="geraet-haushalt",
                password="SecurePass123!",
                role_id=test_role.id,
                is_device_account=True,
            ),
            db=db_session,
            current_user=MagicMock(
                username="admin", id=1, role=test_role,
                get_permissions=lambda: test_role.permissions,
            ),
        )
        assert body.is_device_account is True
        row = (
            await db_session.execute(
                select(User).where(User.username == "geraet-haushalt")
            )
        ).scalar_one()
        assert row.is_device_account is True

    @pytest.mark.database
    async def test_create_defaults_to_a_person(
        self, db_session: AsyncSession, test_role: Role
    ):
        from api.routes import users as users_routes

        body = await users_routes.create_user(
            request=users_routes.CreateUserRequest(
                username="mensch", password="SecurePass123!", role_id=test_role.id
            ),
            db=db_session,
            current_user=MagicMock(
                username="admin", id=1, role=test_role,
                get_permissions=lambda: test_role.permissions,
            ),
        )
        assert body.is_device_account is False

    @pytest.mark.database
    async def test_update_can_flag_and_unflag(
        self, db_session: AsyncSession, test_user: User
    ):
        from api.routes import users as users_routes

        admin = MagicMock(username="admin", id=test_user.id + 1000)
        body = await users_routes.update_user(
            user_id=test_user.id,
            request=users_routes.UpdateUserRequest(is_device_account=True),
            db=db_session,
            current_user=admin,
        )
        assert body.is_device_account is True
        body = await users_routes.update_user(
            user_id=test_user.id,
            request=users_routes.UpdateUserRequest(is_device_account=False),
            db=db_session,
            current_user=admin,
        )
        assert body.is_device_account is False

    @pytest.mark.database
    async def test_update_without_the_field_leaves_it_alone(
        self, db_session: AsyncSession, test_user: User
    ):
        from api.routes import users as users_routes

        test_user.is_device_account = True
        await db_session.commit()
        body = await users_routes.update_user(
            user_id=test_user.id,
            request=users_routes.UpdateUserRequest(first_name="Neu"),
            db=db_session,
            current_user=MagicMock(username="admin", id=test_user.id + 1000),
        )
        assert body.is_device_account is True

    @pytest.mark.database
    async def test_you_cannot_turn_your_own_account_into_a_device(
        self, db_session: AsyncSession, test_user: User
    ):
        """A device account collects no memories and books no presence —
        flagging the account you are logged in with would silently stop your
        own traces."""
        from fastapi import HTTPException

        from api.routes import users as users_routes

        with pytest.raises(HTTPException) as exc:
            await users_routes.update_user(
                user_id=test_user.id,
                request=users_routes.UpdateUserRequest(is_device_account=True),
                db=db_session,
                current_user=MagicMock(username=test_user.username, id=test_user.id),
            )
        assert exc.value.status_code == 400
        await db_session.refresh(test_user)
        assert test_user.is_device_account is False


class TestDeleteRefusesToTakeKnowledgeWithIt:
    """`DELETE /users/{id}` used to 500 on any account that still held data.

    36 foreign keys reference `users.id` WITHOUT `ON DELETE`, so Postgres refuses
    the parent DELETE — and the caller got a 500 that named nothing. Found on
    2026-09-24 while removing a test account.

    The answer is not a cascade: a member's atoms are household-tier knowledge
    the others still read. Nor is it a pre-count of one table — the first fix
    tried that and left the 500 alive for every account with a circle membership
    and no atoms, which on this household is every family member.
    """

    @staticmethod
    async def _plain_role(db: AsyncSession) -> Role:
        """A role WITHOUT `admin`: the victim must not be the last admin, or the
        last-admin guard answers first and this test proves nothing."""
        role = Role(name=f"opfer_rolle_{uuid4().hex[:8]}", permissions=["chat.own"])
        db.add(role)
        await db.flush()
        return role

    async def _victim(self, db: AsyncSession, username: str) -> User:
        u = User(username=username, password_hash="x",
                 role_id=(await self._plain_role(db)).id, is_active=True)
        db.add(u)
        await db.flush()
        return u

    @staticmethod
    def _admin(test_role: Role) -> MagicMock:
        # id far out of the way: the victim is the first row in a fresh test DB
        # and would otherwise trip the self-deletion guard.
        return MagicMock(username="admin", id=999_999, role=test_role,
                         get_permissions=lambda: test_role.permissions)

    @staticmethod
    async def _atom_for(db: AsyncSession, owner_id: int) -> None:
        from models.database import ATOM_TYPE_KG_NODE
        from services.atom_service import AtomService

        aid = await AtomService(db).create_with_source(
            atom_type=ATOM_TYPE_KG_NODE, owner_user_id=owner_id, tier=2,
        )
        await AtomService(db).finalize_source_id(aid, 1)

    @pytest.mark.database
    async def test_refuses_with_409_when_the_account_owns_atoms(
        self, db_session: AsyncSession, test_role: Role
    ):
        from fastapi import HTTPException

        from api.routes import users as users_routes

        victim = await self._victim(db_session, "hat-wissen")
        await self._atom_for(db_session, victim.id)

        with pytest.raises(HTTPException) as err:
            await users_routes.delete_user(
                user_id=victim.id, db=db_session, current_user=self._admin(test_role),
            )
        assert err.value.status_code == 409
        assert err.value.detail["code"] == "user_still_referenced"
        assert err.value.detail["blocked_by"] == "atoms"

        still_there = (await db_session.execute(
            select(User).where(User.id == victim.id)
        )).scalar_one_or_none()
        assert still_there is not None

    @pytest.mark.database
    async def test_refuses_for_a_membership_too_not_just_atoms(
        self, db_session: AsyncSession, test_role: Role
    ):
        """The regression the first fix missed. The household backfill writes a
        PAIRWISE circle_memberships row for every family member, so an account
        can hold nothing but a membership — and `circle_memberships` has three
        blocking FKs to `users.id` of its own."""
        from fastapi import HTTPException

        from api.routes import users as users_routes
        from models.database import CircleMembership

        owner = await self._victim(db_session, "kreis-eigner")
        victim = await self._victim(db_session, "nur-mitglied")
        db_session.add(CircleMembership(
            circle_owner_id=owner.id, member_user_id=victim.id,
            dimension="tier", value="2", granted_by=owner.id,
        ))
        await db_session.flush()

        with pytest.raises(HTTPException) as err:
            await users_routes.delete_user(
                user_id=victim.id, db=db_session, current_user=self._admin(test_role),
            )
        assert err.value.status_code == 409          # kein 500
        assert err.value.detail["blocked_by"] == "circle_memberships"

    @pytest.mark.database
    async def test_deletes_an_account_that_holds_nothing(
        self, db_session: AsyncSession, test_role: Role
    ):
        from api.routes import users as users_routes

        victim = await self._victim(db_session, "leeres-konto")

        with patch("api.routes.users.run_hooks", new=AsyncMock()):
            out = await users_routes.delete_user(
                user_id=victim.id, db=db_session, current_user=self._admin(test_role),
            )
        assert "leeres-konto" in out["message"]
        gone = (await db_session.execute(
            select(User).where(User.id == victim.id)
        )).scalar_one_or_none()
        assert gone is None




async def _with_password(db_session, user, passwort: str = "testpassword123"):
    """Ein ECHTES Passwort setzen.

    🛑 `sample_user_data` traegt einen gefaelschten bcrypt-Hash (Kommentar dort:
    „Fake hash"). `verify_password` schlaegt dagegen immer fehl — ein Test, der
    das Passwort vorlegen muss, pruefte sonst nur, dass die Ablehnung feuert.
    """
    from services.auth_service import get_password_hash

    user.password_hash = get_password_hash(passwort)
    await db_session.commit()
    return passwort

def _admin(user_id: int):
    """Ein Konto MIT `admin` — seit dem Review verlangt das Abschalten eines
    FREMDEN Faktors mehr als `users.manage`."""
    return MagicMock(
        username="admin", id=user_id,
        get_permissions=lambda: ["admin", "users.manage", "users.view"],
    )


def _user_manager(user_id: int):
    """Delegiertes `users.manage` OHNE `admin`. Diesen Prinzipal verteidigt
    `models/permissions.py` an anderer Stelle ausdruecklich gegen
    Rechteausweitung — hier ist er der Angreifer."""
    return MagicMock(
        username="verwalter", id=user_id,
        get_permissions=lambda: ["users.manage", "users.view"],
    )


class TestVoiceSecondFactorConsent:
    """`POST /users/{id}/voice-second-factor` — die Einwilligung in die Stimme
    als zweiten Faktor.

    🛑 Diese Route hat BEWUSST zwei verschiedene Rechte fuer zwei Richtungen.
    Ein ECAPA-Stimmabdruck ist biometrisches Datum (Art. 9 DSGVO); eine
    Anmeldung, die ihn verlangt, ist eine Einwilligung, und die kann niemand
    fuer jemanden anderen geben. Einschalten also nur fuer sich selbst,
    ausschalten fuer jede und jeden mit `users.manage` — denn Ausschalten ist
    der einzige dokumentierte Weg zurueck, wenn ein Mikrofon defekt ist
    (`.claude/rules/auth.md`: es gibt keinen Rueckfall auf Passwort allein).

    Ohne diese Route waere der in PR #1350 beschriebene Wiederherstellungsweg
    („ein Administrator schaltet es ab") reine Theorie: der Zustand haette
    keine API und keine Oberflaeche.
    """

    @pytest.fixture(autouse=True)
    def _auth_on(self, monkeypatch):
        """🛑 Der zweite Faktor ergibt nur mit eingeschalteter Auth einen Sinn.

        `settings.auth_enabled` ist im Prüfstand standardmäßig `False`, und die
        Route verweigert in diesem Modus BEIDE Richtungen mit 401: bei
        abgeschalteter Auth löst `get_user_or_default` jeden Aufrufer auf das
        Administratorkonto auf, es gäbe also kein „Selbst", mit dem man
        einwilligen könnte (Befund F4 des adversarialen Durchgangs).

        Die Tests hier prüfen die Berechtigungslogik einer auth-ON-Instanz —
        also muss der Prüfstand das auch sein. Ohne diese Fixture prüften sie
        nur noch, dass der 401 feuert.
        """
        from utils.config import settings
        monkeypatch.setattr(settings, "auth_enabled", True)

    @staticmethod
    def _path_on():
        """Die instanzweite Haelfte oeffnen.

        🛑 Der Pruefstand faehrt `voice_auth_enabled=False`. Die ANZEIGE meldet
        die instanzweite Haelfte zuerst — voellig richtig, denn ein
        abgeschalteter Sprachweg ist die dominante Wahrheit. Wer die
        kontogebundene Haelfte pruefen will, muss die instanzweite also
        oeffnen; sonst prueft er `voice_path_off` und glaubt, er pruefe das
        Profil. Genau darauf sind diese Tests beim ersten Lauf hereingefallen.
        """
        from utils.config import settings
        return patch.multiple(
            settings, voice_auth_enabled=True, speaker_recognition_enabled=True,
        )


    @staticmethod
    async def _with_voice(db_session: AsyncSession, user: User, embeddings: int = 1):
        """Verknuepft `user` mit einem Sprecherprofil samt `embeddings` Einbettungen."""
        from models.database import SpeakerEmbedding

        speaker = Speaker(name=f"stimme-{uuid4().hex[:8]}")
        db_session.add(speaker)
        await db_session.flush()
        for _ in range(embeddings):
            db_session.add(
                SpeakerEmbedding(speaker_id=speaker.id, embedding="AAAA", sample_duration=2000)
            )
        user.speaker_id = speaker.id
        await db_session.commit()
        return speaker

    @pytest.mark.database
    async def test_you_can_arm_it_for_yourself(
        self, db_session: AsyncSession, test_user: User
    ):
        from api.routes import users as users_routes

        await self._with_voice(db_session, test_user)
        body = await users_routes.set_voice_second_factor(
            user_id=test_user.id,
            request=users_routes.VoiceSecondFactorRequest(enabled=True),
            db=db_session,
            current_user=MagicMock(username=test_user.username, id=test_user.id),
        )
        assert body.voice_second_factor_enabled is True
        await db_session.refresh(test_user)
        assert test_user.voice_second_factor_enabled is True

    @pytest.mark.database
    async def test_an_admin_cannot_impose_it_on_someone_else(
        self, db_session: AsyncSession, test_user: User
    ):
        """Das ist der eigentliche Punkt der Route: eine erzwungene biometrische
        Erfassung waere keine Verwaltung."""
        from fastapi import HTTPException

        from api.routes import users as users_routes

        await self._with_voice(db_session, test_user)
        with pytest.raises(HTTPException) as exc:
            await users_routes.set_voice_second_factor(
                user_id=test_user.id,
                request=users_routes.VoiceSecondFactorRequest(enabled=True),
                db=db_session,
                current_user=MagicMock(username="admin", id=test_user.id + 1000),
            )
        assert exc.value.status_code == 403
        await db_session.refresh(test_user)
        assert test_user.voice_second_factor_enabled is False

    @pytest.mark.database
    async def test_an_admin_can_always_take_it_away(
        self, db_session: AsyncSession, test_user: User
    ):
        """Der Rueckweg ist genau die entgegengesetzte Asymmetrie — sonst waere
        ein defektes Mikrofon eine dauerhafte Aussperrung."""
        from api.routes import users as users_routes

        await self._with_voice(db_session, test_user)
        test_user.voice_second_factor_enabled = True
        await db_session.commit()

        body = await users_routes.set_voice_second_factor(
            user_id=test_user.id,
            request=users_routes.VoiceSecondFactorRequest(enabled=False),
            db=db_session,
            current_user=_admin(test_user.id + 1000),
        )
        assert body.voice_second_factor_enabled is False
        await db_session.refresh(test_user)
        assert test_user.voice_second_factor_enabled is False

    # ------------------------------------------------------------------
    # GET /{id}/voice-second-factor — der Grund, den die Seite vorher nie sah
    # ------------------------------------------------------------------

    @pytest.mark.database
    async def test_the_reason_is_readable_BEFORE_consenting(
        self, db_session: AsyncSession, test_user: User
    ):
        """🛑 Der Kern des Befundes vom 2026-09-29.

        Die Seite „Mein Konto" zeigte einen Grund nur im Zustand „scharf" — also
        nie fuer jemanden, der gerade ueberlegt einzuwilligen. Gemessen im
        Haushalt: bei 6 von 7 Konten war `no_profile` der Blocker, und genau die
        sahen ein blankes „Aus" mit einer Schaltflaeche, die fehlschlagen musste.
        Der Grund muss also OHNE Einwilligung lesbar sein.
        """
        from api.routes import users as users_routes

        assert test_user.voice_second_factor_enabled is False
        assert test_user.speaker_id is None

        with self._path_on():
            state = await users_routes.get_voice_second_factor(
                user_id=test_user.id,
                db=db_session,
                current_user=test_user,
            )
        assert state.enabled is False
        assert state.blocker == NO_PROFILE

    @pytest.mark.database
    async def test_no_blocker_once_the_profile_carries_a_sample(
        self, db_session: AsyncSession, test_user: User
    ):
        """Gegenprobe: ohne sie waere der Test oben auch dann gruen, wenn die
        Route IMMER `no_profile` meldete."""
        from api.routes import users as users_routes

        await self._with_voice(db_session, test_user, embeddings=1)
        with self._path_on():
            state = await users_routes.get_voice_second_factor(
                user_id=test_user.id, db=db_session, current_user=test_user,
            )
        assert state.blocker is None

    @pytest.mark.database
    async def test_a_profile_without_samples_reads_as_such(
        self, db_session: AsyncSession, test_user: User
    ):
        """Die vier Gruende muessen UNTERSCHEIDBAR herauskommen — sonst kann die
        Oberflaeche sie nicht verschieden uebersetzen."""
        from api.routes import users as users_routes

        await self._with_voice(db_session, test_user, embeddings=0)
        with self._path_on():
            state = await users_routes.get_voice_second_factor(
                user_id=test_user.id, db=db_session, current_user=test_user,
            )
        assert state.blocker == NO_EMBEDDINGS

    @pytest.mark.database
    async def test_the_instance_half_is_reported_too(
        self, db_session: AsyncSession, test_user: User
    ):
        """`voice_path_off` gehoert zum ANZEIGEN dazu, obwohl es das Einschalten
        bewusst nicht blockiert — sonst kann die Seite ein Ruhen nicht benennen.
        """
        from api.routes import users as users_routes
        from utils.config import settings

        await self._with_voice(db_session, test_user, embeddings=1)
        with patch.object(settings, "voice_auth_enabled", False):
            state = await users_routes.get_voice_second_factor(
                user_id=test_user.id, db=db_session, current_user=test_user,
            )
        assert state.blocker == PATH_OFF

        # 🛑 Der Sprachweg muss dafuer AN sein, sonst gewinnt `PATH_OFF` und der
        # Test behauptete etwas ueber die Erkennung, ohne sie zu beruehren.
        with patch.multiple(
            settings, voice_auth_enabled=True, speaker_recognition_enabled=False,
        ):
            state = await users_routes.get_voice_second_factor(
                user_id=test_user.id, db=db_session, current_user=test_user,
            )
        assert state.blocker == RECOGNITION_OFF

    @pytest.mark.database
    async def test_reading_someone_elses_state_is_refused(
        self, db_session: AsyncSession, test_user: User
    ):
        """Der Grund nennt eine Eigenschaft des Sprecherprofils einer Person —
        er gehoert ihr. Auch `admin` liest ihn hier nicht."""
        from fastapi import HTTPException

        from api.routes import users as users_routes

        with pytest.raises(HTTPException) as exc:
            await users_routes.get_voice_second_factor(
                user_id=test_user.id,
                db=db_session,
                current_user=_admin(test_user.id + 1000),
            )
        assert exc.value.status_code == 403

    @pytest.mark.database
    async def test_reading_needs_an_authenticated_instance(
        self, db_session: AsyncSession, test_user: User
    ):
        """Dieselbe Regel wie beim Schreiben: ohne Authentifizierung gibt es kein
        „selbst", also auch nichts eigenes zu lesen."""
        from fastapi import HTTPException

        from api.routes import users as users_routes
        from utils.config import settings

        with patch.object(settings, "auth_enabled", False):
            with pytest.raises(HTTPException) as exc:
                await users_routes.get_voice_second_factor(
                    user_id=test_user.id, db=db_session, current_user=test_user,
                )
        assert exc.value.status_code == 401

    @pytest.mark.database
    async def test_arming_without_a_speaker_profile_is_refused(
        self, db_session: AsyncSession, test_user: User
    ):
        """`POST /auth/voice` prueft 1:1 gegen das verknuepfte Profil und
        verweigert fail-closed, wenn keines da ist. Ohne diese Sperre waere das
        Einschalten eine Selbstaussperrung."""
        from fastapi import HTTPException

        from api.routes import users as users_routes

        assert test_user.speaker_id is None
        with pytest.raises(HTTPException) as exc:
            await users_routes.set_voice_second_factor(
                user_id=test_user.id,
                request=users_routes.VoiceSecondFactorRequest(enabled=True),
                db=db_session,
                current_user=MagicMock(username=test_user.username, id=test_user.id),
            )
        assert exc.value.status_code == 409
        # 🛑 Der CODE ist der Vertrag, nicht der Satz. Hier standen drei
        # hartkodierte englische Saetze, und sie waren die einzige Stelle, an
        # der die Person den Grund erfuhr — ein deutschsprachiges Mitglied las
        # Englisch. Die Oberflaeche uebersetzt jetzt diesen Code; pruefte der
        # Test nur den Statuscode, koennte er unbemerkt wieder zu Prosa werden.
        assert exc.value.detail == NO_PROFILE
        await db_session.refresh(test_user)
        assert test_user.voice_second_factor_enabled is False

    @pytest.mark.database
    async def test_a_profile_without_embeddings_is_the_same_trap(
        self, db_session: AsyncSession, test_user: User
    ):
        """Ein verknuepftes Profil, gegen das nichts verglichen werden kann,
        sperrt genauso aus wie gar keines."""
        from fastapi import HTTPException

        from api.routes import users as users_routes

        await self._with_voice(db_session, test_user, embeddings=0)
        with pytest.raises(HTTPException) as exc:
            await users_routes.set_voice_second_factor(
                user_id=test_user.id,
                request=users_routes.VoiceSecondFactorRequest(enabled=True),
                db=db_session,
                current_user=MagicMock(username=test_user.username, id=test_user.id),
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == NO_EMBEDDINGS

    @pytest.mark.database
    async def test_disarming_needs_no_profile_at_all(
        self, db_session: AsyncSession, test_user: User
    ):
        """Der Wiederherstellungsweg darf nicht an derselben Bedingung haengen
        wie der Hinweg — sonst waere ein geloeschtes Sprecherprofil endgueltig."""
        from api.routes import users as users_routes

        test_user.voice_second_factor_enabled = True
        await db_session.commit()
        assert test_user.speaker_id is None

        body = await users_routes.set_voice_second_factor(
            user_id=test_user.id,
            request=users_routes.VoiceSecondFactorRequest(enabled=False),
            db=db_session,
            current_user=_admin(test_user.id + 1000),
        )
        assert body.voice_second_factor_enabled is False

    @pytest.mark.database
    async def test_an_unknown_account_is_a_404_not_a_500(
        self, db_session: AsyncSession
    ):
        from fastapi import HTTPException

        from api.routes import users as users_routes

        with pytest.raises(HTTPException) as exc:
            await users_routes.set_voice_second_factor(
                user_id=987654,
                request=users_routes.VoiceSecondFactorRequest(enabled=False),
                db=db_session,
                current_user=_admin(1),
            )
        assert exc.value.status_code == 404

    @pytest.mark.database
    async def test_every_other_endpoint_reports_the_state_too(
        self, db_session: AsyncSession, test_user: User
    ):
        """🛑 Negativkontrolle gegen den Fehler, den ich beim Bau fast gemacht
        haette: `UserResponse` wird an sieben Stellen INLINE aufgebaut. Das neue
        Feld hat den Standard `False` — ohne Ergaenzung an jeder Stelle haette
        die Liste den Zustand falsch gemeldet, und die Oberflaeche haette einen
        Schalter gezeigt, der aus aussieht, obwohl er an ist."""
        from api.routes import users as users_routes

        await self._with_voice(db_session, test_user)
        test_user.voice_second_factor_enabled = True
        await db_session.commit()

        admin = MagicMock(username="admin", id=test_user.id + 1000)
        one = await users_routes.get_user(
            user_id=test_user.id, db=db_session, current_user=admin
        )
        assert one.voice_second_factor_enabled is True

        listing = await users_routes.list_users(db=db_session, current_user=admin)
        mine = [u for u in listing.users if u.id == test_user.id]
        assert mine and mine[0].voice_second_factor_enabled is True

    @pytest.mark.database
    async def test_a_user_manager_cannot_strip_someone_elses_factor(
        self, db_session: AsyncSession, test_user: User
    ):
        """🛑 Der Rueckweg ist zugleich ein Angriffsweg.

        Wer einem fremden Konto den zweiten Faktor nimmt, senkt dessen Anmeldung
        still auf Passwort allein und kann sich danach an diesem Passwort
        versuchen. Das ist MEHR als ein Passwort-Zuruecksetzen — das allein kommt
        an einem scharfen zweiten Faktor nicht vorbei. `users.manage` ist ein
        delegierbares Recht ohne `admin`, also verlangt diese Richtung `admin`.
        """
        from fastapi import HTTPException

        from api.routes import users as users_routes

        test_user.voice_second_factor_enabled = True
        await db_session.commit()

        with pytest.raises(HTTPException) as exc:
            await users_routes.set_voice_second_factor(
                user_id=test_user.id,
                request=users_routes.VoiceSecondFactorRequest(enabled=False),
                db=db_session,
                current_user=_user_manager(test_user.id + 1000),
            )
        assert exc.value.status_code == 403
        await db_session.refresh(test_user)
        assert test_user.voice_second_factor_enabled is True

    @pytest.mark.database
    async def test_a_user_manager_may_still_withdraw_their_OWN_consent(
        self, db_session: AsyncSession, test_user: User
    ):
        """Die Gegenprobe zum Test darueber: den eigenen Faktor zurueckzunehmen
        ist niemandes Rechteausweitung und darf nicht an `admin` haengen."""
        from api.routes import users as users_routes

        test_user.voice_second_factor_enabled = True
        await _with_password(db_session, test_user)

        body = await users_routes.set_voice_second_factor(
            user_id=test_user.id,
            request=users_routes.VoiceSecondFactorRequest(
                enabled=False, current_password="testpassword123"
            ),
            db=db_session,
            current_user=_user_manager(test_user.id),
        )
        assert body.voice_second_factor_enabled is False

    @pytest.mark.database
    async def test_without_a_caller_nothing_is_decided(
        self, db_session: AsyncSession, test_user: User
    ):
        """🛑 Dieser Test stand vorher auf dem Kopf — er verlangte, dass ein
        Aufruf OHNE Aufrufer den Faktor eines fremden Kontos abschaltet, und
        war gruen.

        Grund: die `is not None`-Zusaetze fielen unsymmetrisch. Beim Einschalten
        schloss `None` zu (nicht ich -> 403), beim Ausschalten riss es auf — die
        ganze `elif`-Bedingung wurde False und der Schreibvorgang lief durch.
        Jeder haette jedem den zweiten Faktor nehmen koennen, und ein gruener
        Test hat das festgeschrieben.

        Heute unerreichbar (`get_user_or_default` liefert immer einen Nutzer,
        bei abgeschalteter Auth den Administrator) — aber genau deshalb muss der
        Riegel stehen und dieser Test ihn belegen: eine Fehlertoleranz, die auf
        einer Seite zu- und auf der anderen aufgeht, sieht nach Absicherung aus.
        """
        from fastapi import HTTPException

        from api.routes import users as users_routes

        test_user.voice_second_factor_enabled = True
        await db_session.commit()

        with pytest.raises(HTTPException) as exc:
            await users_routes.set_voice_second_factor(
                user_id=test_user.id,
                request=users_routes.VoiceSecondFactorRequest(enabled=False),
                db=db_session,
                current_user=None,
            )
        assert exc.value.status_code == 401
        await db_session.refresh(test_user)
        assert test_user.voice_second_factor_enabled is True, "nichts darf geschrieben sein"

    @pytest.mark.database
    async def test_arming_is_refused_while_recognition_is_off(
        self, db_session: AsyncSession, test_user: User, monkeypatch
    ):
        """Auch das ist eine Falle: die Einwilligung waere scharf, aber
        `/auth/voice` verweigert kategorisch, solange die Erkennung aus ist."""
        from fastapi import HTTPException

        from api.routes import users as users_routes
        from utils.config import settings

        await self._with_voice(db_session, test_user)
        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", False)

        with pytest.raises(HTTPException) as exc:
            await users_routes.set_voice_second_factor(
                user_id=test_user.id,
                request=users_routes.VoiceSecondFactorRequest(enabled=True),
                db=db_session,
                current_user=MagicMock(username=test_user.username, id=test_user.id),
            )
        assert exc.value.status_code == 409

    @pytest.mark.database
    async def test_arming_stays_possible_while_the_voice_path_is_still_off(
        self, db_session: AsyncSession, test_user: User, monkeypatch
    ):
        """🛑 Gegenkontrolle gegen einen zu strengen Riegel.

        `VOICE_AUTH_ENABLED=false` darf das Einschalten NICHT blockieren, sonst
        waere die Reihenfolge des Cutovers unmoeglich: erst die Einwilligungen
        einsammeln, dann das Flag umlegen. Dass die Huerde solange ruht, ist
        dokumentiert und gewollt.
        """
        from api.routes import users as users_routes
        from utils.config import settings

        await self._with_voice(db_session, test_user)
        monkeypatch.setattr(settings, "voice_auth_enabled", False)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)

        body = await users_routes.set_voice_second_factor(
            user_id=test_user.id,
            request=users_routes.VoiceSecondFactorRequest(enabled=True),
            db=db_session,
            current_user=MagicMock(username=test_user.username, id=test_user.id),
        )
        assert body.voice_second_factor_enabled is True


class TestTheUnlockButtonSeesBothCounters:
    """🛑 Die Oberfläche meldete auf dem WIEDERHERSTELLUNGSBILDSCHIRM das
    Gegenteil der Wahrheit.

    `/auth/voice` zählt Fehlversuche unter `voice2fa:<id>`, die Verwaltung
    fragte unter dem Benutzernamen. Beide trafen sich nie: die Liste zeigte
    „nicht gesperrt", und der Entsperr-Knopf meldete `cleared_keys=0`, während
    die Person tatsächlich nicht hereinkam.

    Die getrennten Namensräume bleiben — ein Fehlversuch der Stimme darf den
    Passwortpfad nicht mitsperren, sonst wäre der zweite Faktor ein Weg, jemanden
    mit fremden Mitteln aus seinem Konto zu drängen. Nur die Anzeige wird ehrlich.
    """

    @pytest.mark.database
    async def test_a_voice_lock_shows_up_in_the_single_user_view(
        self, db_session: AsyncSession, test_user: User, monkeypatch
    ):
        from api.routes import users as users_routes
        from services.voice_factor_preconditions import voice_factor_lock_id

        voice_id = voice_factor_lock_id(test_user.id)
        asked: list[str] = []

        async def _has_any_lock(username: str) -> bool:
            asked.append(username)
            return username == voice_id

        monkeypatch.setattr(users_routes.login_lockout, "has_any_lock", _has_any_lock)

        body = await users_routes.get_user(
            user_id=test_user.id, db=db_session,
            current_user=MagicMock(username="admin", id=1),
        )
        assert body.locked_out is True, "die Stimmsperre muss sichtbar sein"
        assert voice_id in asked

    @pytest.mark.database
    async def test_the_unlock_button_clears_both(
        self, db_session: AsyncSession, test_user: User, monkeypatch
    ):
        from api.routes import users as users_routes
        from services.voice_factor_preconditions import voice_factor_lock_id

        cleared: list[str] = []

        async def _unlock(username: str) -> int:
            cleared.append(username)
            return 1

        monkeypatch.setattr(users_routes.login_lockout, "unlock", _unlock)

        await users_routes.unlock_user(
            user_id=test_user.id, db=db_session,
            current_user=MagicMock(username="admin", id=test_user.id + 1000),
        )
        assert cleared == [test_user.username, voice_factor_lock_id(test_user.id)]

    @pytest.mark.database
    async def test_an_unlocked_account_still_reports_false(
        self, db_session: AsyncSession, test_user: User, monkeypatch
    ):
        """Gegenprobe: sonst bewiesen die Tests darüber nur, dass `locked_out`
        jetzt immer `True` sagt."""
        from api.routes import users as users_routes

        async def _has_any_lock(_username: str) -> bool:
            return False

        monkeypatch.setattr(users_routes.login_lockout, "has_any_lock", _has_any_lock)

        body = await users_routes.get_user(
            user_id=test_user.id, db=db_session,
            current_user=MagicMock(username="admin", id=1),
        )
        assert body.locked_out is False


def _plain_member(user_id: int):
    """Ein Haushaltsmitglied: KEIN `users.manage`, KEIN `admin`. Genau die
    Person, für die die Einwilligung gedacht ist — und die sie bis zum
    2026-09-28 nicht erteilen konnte."""
    return MagicMock(
        username="familie", id=user_id,
        get_permissions=lambda: ["chat.own", "kb.shared"],
    )


class TestTheConsentBelongsToThePerson:
    """🛑 Die Einwilligung ist höchstpersönlich — also muss die Person sie
    erteilen können, nicht nur eine Administratorin.

    Vor dem 2026-09-28 stand `require_permission(USERS_MANAGE)` vor BEIDEN
    Richtungen. Damit war die Einwilligung in die Verarbeitung biometrischer
    Daten (Art. 9 DSGVO) ausgerechnet für die Person unerreichbar, um deren
    Stimme es geht: ein Haushaltsmitglied konnte weder einwilligen noch seinen
    Zustand sehen. Eine Einwilligung, die nur ein Dritter erteilen kann, ist
    keine.

    Sicherheitslage dabei unverändert: den eigenen Faktor scharf zu stellen
    fügt eine ZUSÄTZLICHE Hürde am eigenen Konto hinzu, und ihn zurückzunehmen
    setzt voraus, angemeldet zu sein — was bei scharfem Faktor bereits bedeutet,
    ihn bestanden zu haben.
    """

    @pytest.fixture(autouse=True)
    def _auth_on(self, monkeypatch):
        """🛑 Der zweite Faktor ergibt nur mit eingeschalteter Auth einen Sinn.

        `settings.auth_enabled` ist im Prüfstand standardmäßig `False`, und die
        Route verweigert in diesem Modus BEIDE Richtungen mit 401: bei
        abgeschalteter Auth löst `get_user_or_default` jeden Aufrufer auf das
        Administratorkonto auf, es gäbe also kein „Selbst", mit dem man
        einwilligen könnte (Befund F4 des adversarialen Durchgangs).

        Die Tests hier prüfen die Berechtigungslogik einer auth-ON-Instanz —
        also muss der Prüfstand das auch sein. Ohne diese Fixture prüften sie
        nur noch, dass der 401 feuert.
        """
        from utils.config import settings
        monkeypatch.setattr(settings, "auth_enabled", True)


    @pytest.mark.database
    async def test_a_plain_member_may_arm_their_own(
        self, db_session: AsyncSession, test_user: User
    ):
        from api.routes import users as users_routes

        await TestVoiceSecondFactorConsent._with_voice(db_session, test_user)
        body = await users_routes.set_voice_second_factor(
            user_id=test_user.id,
            request=users_routes.VoiceSecondFactorRequest(enabled=True),
            db=db_session,
            current_user=_plain_member(test_user.id),
        )
        assert body.voice_second_factor_enabled is True

    @pytest.mark.database
    async def test_a_plain_member_may_withdraw_their_own(
        self, db_session: AsyncSession, test_user: User
    ):
        from api.routes import users as users_routes

        test_user.voice_second_factor_enabled = True
        await _with_password(db_session, test_user)
        body = await users_routes.set_voice_second_factor(
            user_id=test_user.id,
            request=users_routes.VoiceSecondFactorRequest(
                enabled=False, current_password="testpassword123"
            ),
            db=db_session,
            current_user=_plain_member(test_user.id),
        )
        assert body.voice_second_factor_enabled is False

    @pytest.mark.database
    async def test_a_plain_member_still_cannot_touch_a_stranger(
        self, db_session: AsyncSession, test_user: User
    ):
        """🛑 Die Gegenkontrolle zur Lockerung: die Berechtigung fällt NUR für
        das eigene Konto. Ein fremdes abzuschalten verlangt weiter `admin` —
        sonst wäre der Rückweg ein Angriffsweg für jedermann."""
        from fastapi import HTTPException

        from api.routes import users as users_routes

        test_user.voice_second_factor_enabled = True
        await db_session.commit()

        with pytest.raises(HTTPException) as exc:
            await users_routes.set_voice_second_factor(
                user_id=test_user.id,
                request=users_routes.VoiceSecondFactorRequest(enabled=False),
                db=db_session,
                current_user=_plain_member(test_user.id + 1000),
            )
        assert exc.value.status_code == 403
        await db_session.refresh(test_user)
        assert test_user.voice_second_factor_enabled is True

    @pytest.mark.database
    async def test_a_device_account_has_no_voice(
        self, db_session: AsyncSession, test_user: User
    ):
        """Ein Gerätekonto spricht nicht und meldet sich nicht über
        `/auth/login` an. Eine Einwilligung, die es nie einlösen kann, ist ein
        Zustand, den niemand gebrauchen kann — lieber hier sagen als später
        raten."""
        from fastapi import HTTPException

        from api.routes import users as users_routes

        await TestVoiceSecondFactorConsent._with_voice(db_session, test_user)
        test_user.is_device_account = True
        await db_session.commit()

        with pytest.raises(HTTPException) as exc:
            await users_routes.set_voice_second_factor(
                user_id=test_user.id,
                request=users_routes.VoiceSecondFactorRequest(enabled=True),
                db=db_session,
                current_user=_plain_member(test_user.id),
            )
        assert exc.value.status_code == 409

    def test_the_route_no_longer_demands_users_manage(self):
        """Strukturprüfung gegen das Zurückrutschen: kehrt
        `require_permission(USERS_MANAGE)` an diese Route zurück, ist die
        Einwilligung wieder fremdbestimmt — und das Verhalten oben fiele mit
        einem 403 aus, dessen Ursache man in der Route suchen müsste."""
        import inspect

        from api.routes import users as users_routes

        src = inspect.getsource(users_routes.set_voice_second_factor)
        sig = src[: src.index('"""')]
        assert "get_user_or_default" in sig
        assert "USERS_MANAGE" not in sig


    @pytest.mark.database
    async def test_it_does_not_leak_which_user_ids_exist(
        self, db_session: AsyncSession, test_user: User
    ):
        """🛑 Aufzählungsorakel, entstanden durch die Lockerung selbst.

        Das 404 stand VOR jeder Berechtigungsprüfung. Für einen Unberechtigten
        unterschied die Antwort damit „gibt es nicht" (404) von „gibt es, nicht
        deins" (403) — über den gesamten Id-Raum, und offen für jedes
        angemeldete Mitglied. Vorher verwehrte `require_permission(USERS_MANAGE)`
        den Zutritt vor der Abfrage; mit der Lockerung fällt dieser Schutz weg,
        also muss die Reihenfolge ihn ersetzen.

        Beide Fälle müssen für einen Unberechtigten gleich aussehen.
        """
        from fastapi import HTTPException

        from api.routes import users as users_routes

        fremder = _plain_member(test_user.id + 1000)

        with pytest.raises(HTTPException) as vorhanden:
            await users_routes.set_voice_second_factor(
                user_id=test_user.id,
                request=users_routes.VoiceSecondFactorRequest(enabled=True),
                db=db_session, current_user=fremder,
            )
        with pytest.raises(HTTPException) as gibtsnicht:
            await users_routes.set_voice_second_factor(
                user_id=987654,
                request=users_routes.VoiceSecondFactorRequest(enabled=True),
                db=db_session, current_user=fremder,
            )
        assert vorhanden.value.status_code == gibtsnicht.value.status_code == 403, (
            "existierend und nicht existierend muessen fuer einen Unberechtigten "
            "ununterscheidbar sein"
        )


    @pytest.mark.database
    async def test_with_auth_off_there_is_no_self_to_consent_with(
        self, db_session: AsyncSession, test_user: User, monkeypatch
    ):
        """🛑 Regression dieser Lockerung, gefunden im adversarialen Durchgang.

        Bei `AUTH_ENABLED=false` löst `get_user_or_default` JEDEN Aufrufer auf
        das Administratorkonto auf. `is_self` wäre damit wahr, und wer den Port
        erreicht, könnte dem Administrator den zweiten Faktor auferlegen. Die
        Einschaltrichtung prüft `voice_auth_enabled` bewusst nicht — der
        Schreibvorgang ginge also auch bei ruhendem Sprachweg durch und würde
        scharf, sobald jemand `AUTH_ENABLED=true` setzt.

        Vorher lieferte `require_permission` in diesem Modus `None`, `is_self`
        war falsch, und das Einschalten endete immer mit 403. Eine Einwilligung
        nach Art. 9 DSGVO verlangt eine Person; „irgendwer am Port, aufgelöst
        auf admin" ist keine.
        """
        from fastapi import HTTPException

        from api.routes import users as users_routes
        from utils.config import settings

        # Ueberschreibt die `_auth_on`-Fixture dieser Klasse: DIESER Test will
        # gerade den auth-off-Fall.
        monkeypatch.setattr(settings, "auth_enabled", False)
        await TestVoiceSecondFactorConsent._with_voice(db_session, test_user)

        for enabled in (True, False):
            with pytest.raises(HTTPException) as exc:
                await users_routes.set_voice_second_factor(
                    user_id=test_user.id,
                    request=users_routes.VoiceSecondFactorRequest(enabled=enabled),
                    db=db_session,
                    current_user=_plain_member(test_user.id),
                )
            assert exc.value.status_code == 401, f"enabled={enabled}"
        await db_session.refresh(test_user)
        assert test_user.voice_second_factor_enabled is False

    @pytest.mark.database
    async def test_removing_a_consent_is_logged_louder_than_granting_one(
        self, db_session: AsyncSession, test_user: User
    ):
        """🛑 Das Entfernen wiegt schwerer als das Erteilen: es senkt ein Konto
        auf Passwort allein.

        Derselbe Subsystem-Prüfer schreibt bereits WARNING, wenn die Hürde von
        SELBST ruht (`voice_factor_preconditions`). Dass ein Mensch sie
        wegnimmt, darf nicht leiser protokolliert werden als dass sie von allein
        einschläft — sonst ist der lauteste Eintrag der harmloseste Vorgang.
        """
        from loguru import logger as loguru_logger

        from api.routes import users as users_routes

        await TestVoiceSecondFactorConsent._with_voice(db_session, test_user)
        await _with_password(db_session, test_user)
        ich = _plain_member(test_user.id)

        gesehen: list[tuple[str, str]] = []
        sink = loguru_logger.add(
            lambda m: gesehen.append((m.record["level"].name, m.record["message"])),
            level="INFO",
        )
        try:
            await users_routes.set_voice_second_factor(
                user_id=test_user.id, db=db_session, current_user=ich,
                request=users_routes.VoiceSecondFactorRequest(enabled=True),
            )
            await users_routes.set_voice_second_factor(
                user_id=test_user.id, db=db_session, current_user=ich,
                request=users_routes.VoiceSecondFactorRequest(
                    enabled=False, current_password="testpassword123"
                ),
            )
        finally:
            loguru_logger.remove(sink)

        ein = [m for lvl, m in gesehen if "enabled for user" in m]
        weg = [(lvl, m) for lvl, m in gesehen if "REMOVED" in m]
        assert ein, "das Erteilen muss protokolliert sein"
        assert weg, "das Entfernen muss protokolliert sein"
        assert weg[0][0] == "WARNING", f"Entfernen war nur {weg[0][0]}"

    @pytest.mark.database
    async def test_removing_your_own_factor_needs_the_password(
        self, db_session: AsyncSession, test_user: User
    ):
        """🛑 Befund 7 des adversarialen Durchgangs, hier festgenagelt.

        Das Einschalten hebt `token_epoch` NICHT an (ein Epoch-Sprung würde die
        Person im Moment des Einwilligens abmelden — auf einer Ein-Admin-Instanz
        mit klemmendem Sprachweg eine sofortige Aussperrung). Folge: ein Token
        von VOR der Einwilligung überlebt sie, erneuert sich über
        `/auth/refresh` (das den Faktor nicht prüft) und dürfte ihn sonst
        dauerhaft entfernen — genau das, wogegen er schützt. Vorher verlangte
        das `users.manage`; mit der Lockerung fällt dieser Schutz weg.

        Das Passwort ersetzt ihn: wer nur ein Token erbeutet hat, kommt nicht
        durch.
        """
        from fastapi import HTTPException

        from api.routes import users as users_routes

        test_user.voice_second_factor_enabled = True
        await _with_password(db_session, test_user)
        ich = _plain_member(test_user.id)

        for falsch in (None, "", "das-falsche-passwort"):
            with pytest.raises(HTTPException) as exc:
                await users_routes.set_voice_second_factor(
                    user_id=test_user.id,
                    request=users_routes.VoiceSecondFactorRequest(
                        enabled=False, current_password=falsch
                    ),
                    db=db_session, current_user=ich,
                )
            assert exc.value.status_code == 400, f"bei {falsch!r}"
            await db_session.refresh(test_user)
            assert test_user.voice_second_factor_enabled is True, "nichts darf geschrieben sein"

    @pytest.mark.database
    async def test_an_admin_needs_no_password_for_a_stranger(
        self, db_session: AsyncSession, test_user: User
    ):
        """Gegenkontrolle: der Riegel gilt nur fürs EIGENE Konto. Eine
        Administratorin kennt das fremde Passwort nicht — dort steht `admin` als
        Schutz, und der Wiederherstellungsweg darf daran nicht scheitern."""
        from api.routes import users as users_routes

        test_user.voice_second_factor_enabled = True
        await db_session.commit()

        body = await users_routes.set_voice_second_factor(
            user_id=test_user.id,
            request=users_routes.VoiceSecondFactorRequest(enabled=False),
            db=db_session,
            current_user=_admin(test_user.id + 1000),
        )
        assert body.voice_second_factor_enabled is False

    @pytest.mark.database
    async def test_arming_needs_no_password(
        self, db_session: AsyncSession, test_user: User
    ):
        """Nur das ENTFERNEN ist der Angriffsweg. Beim Erteilen die Eingabe zu
        verlangen wäre Reibung ohne Gewinn."""
        from api.routes import users as users_routes

        await TestVoiceSecondFactorConsent._with_voice(db_session, test_user)
        body = await users_routes.set_voice_second_factor(
            user_id=test_user.id,
            request=users_routes.VoiceSecondFactorRequest(enabled=True),
            db=db_session, current_user=_plain_member(test_user.id),
        )
        assert body.voice_second_factor_enabled is True
