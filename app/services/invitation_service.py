"""Invitation policy and one-time codes. Raw codes never persist."""
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.repositories.database import AppSettingRecord, InvitationRecord, SignupThrottleRecord, UserRecord
from app.services.admin_service import SettingsConflict, audit


class InvitationError(ValueError):
    pass


class SignupRateLimited(InvitationError):
    pass


@dataclass(frozen=True)
class SignupPolicy:
    required: bool
    version: int


def signup_policy(db, settings):
    row = db.get(AppSettingRecord, "signup")
    return SignupPolicy(row.value["invite_only"] if row else settings.invite_only_signup,
                        row.version if row else 0)


def code_digest(code):
    return hashlib.sha256(code.strip().encode()).hexdigest()


def require_actor(db, principal):
    user = db.scalar(select(UserRecord).where(UserRecord.id == principal.id).with_for_update())
    if user is None or user.disabled or user.role != "admin":
        raise PermissionError("Admin access required.")


class InvitationService:
    def __init__(self, factory, settings):
        self.factory, self.settings = factory, settings

    def policy(self):
        with self.factory() as db:
            return signup_policy(db, self.settings)

    def save_policy(self, principal, required, version):
        try:
            with self.factory() as db:
                require_actor(db, principal)
                current = signup_policy(db, self.settings)
                if current.version != version:
                    raise SettingsConflict("Registration settings changed. Reload before saving.")
                value = {"invite_only": required}
                if current.version == 0:
                    db.add(AppSettingRecord(key="signup", value=value, version=1))
                else:
                    changed = db.execute(update(AppSettingRecord).where(
                        AppSettingRecord.key == "signup", AppSettingRecord.version == version
                    ).values(value=value, version=version + 1))
                    if changed.rowcount != 1:
                        raise SettingsConflict("Registration settings changed. Reload before saving.")
                audit(db, principal.id, "signup.policy", "signup", {"invite_only": current.required},
                      value, "Updated registration policy")
                db.commit()
        except IntegrityError as exc:
            raise SettingsConflict("Registration settings changed. Reload before saving.") from exc

    def generate(self, principal):
        code = secrets.token_urlsafe(24)
        with self.factory() as db:
            require_actor(db, principal)
            invitation = InvitationRecord(id=str(uuid4()), code_hash=code_digest(code),
                                          suffix=code[-8:], created_by=principal.id)
            db.add(invitation)
            audit(db, principal.id, "invitation.create", invitation.id, {},
                  {"status": "available", "suffix": invitation.suffix}, "Generated invitation")
            db.commit()
        return code

    def deactivate(self, principal, invitation_id):
        with self.factory() as db:
            require_actor(db, principal)
            changed = db.execute(update(InvitationRecord).where(
                InvitationRecord.id == invitation_id, InvitationRecord.used_at.is_(None),
                InvitationRecord.deactivated_at.is_(None),
            ).values(deactivated_at=datetime.now(UTC), deactivated_by=principal.id))
            if changed.rowcount != 1:
                raise InvitationError("Invitation is unavailable, already used, or deactivated.")
            audit(db, principal.id, "invitation.deactivate", invitation_id,
                  {"status": "available"}, {"status": "deactivated"}, "Deactivated invitation")
            db.commit()

    def recent(self, page=1):
        creator, recipient = aliased(UserRecord), aliased(UserRecord)
        with self.factory() as db:
            return db.execute(select(InvitationRecord, creator.email, recipient.email).join(
                creator, creator.id == InvitationRecord.created_by).outerjoin(
                recipient, recipient.id == InvitationRecord.used_by).order_by(
                InvitationRecord.created_at.desc(), InvitationRecord.id.desc()
            ).offset((page - 1) * 50).limit(50)).all()

    def check_rate_limit(self, address):
        # Fixed ten-minute window, shared across processes. Do not trust forwarded IP headers.
        now = datetime.now(UTC)
        window = int(now.timestamp()) // 600
        key = hashlib.sha256(f"{address}:{window}".encode()).hexdigest()
        with self.factory() as db:
            db.execute(delete(SignupThrottleRecord).where(SignupThrottleRecord.expires_at <= now))
            row = db.execute(update(SignupThrottleRecord).where(SignupThrottleRecord.key == key)
                             .values(count=SignupThrottleRecord.count + 1)
                             .returning(SignupThrottleRecord.count)).scalar_one_or_none()
            if row is None:
                try:
                    with db.begin_nested():
                        db.add(SignupThrottleRecord(key=key, count=1,
                                                   expires_at=now + timedelta(minutes=20)))
                        db.flush()
                    row = 1
                except IntegrityError:
                    row = db.execute(update(SignupThrottleRecord).where(SignupThrottleRecord.key == key)
                                     .values(count=SignupThrottleRecord.count + 1)
                                     .returning(SignupThrottleRecord.count)).scalar_one()
            db.commit()
        if row > 20:
            raise SignupRateLimited("Too many registration attempts. Please try again in ten minutes.")

    def validate(self, db, code):
        invitation = db.scalar(select(InvitationRecord).where(
            InvitationRecord.code_hash == code_digest(code), InvitationRecord.used_at.is_(None),
            InvitationRecord.deactivated_at.is_(None)))
        if invitation is None:
            raise InvitationError("A valid, unused invitation code is required.")
        return invitation.id
