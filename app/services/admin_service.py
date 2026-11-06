"""Application administration; credentials and AWS permissions stay outside this service."""
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.repositories.database import AdminAuditRecord, AppSettingRecord, AuthSessionRecord, UserRecord


class SettingsConflict(ValueError):
    pass


class ModelsUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelSettings:
    default: str
    models: tuple[str, ...]
    version: int

def audit(db, actor, action, target, before, after, reason):
    db.add(AdminAuditRecord(id=str(uuid4()), actor=actor, action=action, target=target,
                            before=before, after=after, reason=reason))


def assign_role(factory, issuer: str, subject: str, role: str, *, actor: str,
                reason: str, apply: bool = False) -> dict:
    """Operator-only role assignment by exact verified identity; dry run by default."""
    if role not in {"user", "admin"}:
        raise ValueError("Unknown role.")
    if not issuer or not subject or not actor or not reason.strip() or len(reason) > 500:
        raise ValueError("An exact identity, operator, and reason (1–500 characters) are required.")
    with factory() as db:
        user = db.scalar(select(UserRecord).where(
            UserRecord.issuer == issuer, UserRecord.subject == subject).with_for_update())
        if user is None:
            raise ValueError("Identity not found. Sign in to the app first and check issuer and subject.")
        if user.disabled and role == "admin":
            raise ValueError("Cannot grant admin access to a disabled user.")
        result = {"user_id": user.id, "email": user.email, "before": user.role, "after": role}
        if apply and user.role != role:
            audit(db, actor, "role.change", user.id, {"role": user.role}, {"role": role}, reason.strip())
            user.role = role
            # Promotion requires a fresh sign-in; revocation also kills stolen sessions.
            db.execute(delete(AuthSessionRecord).where(AuthSessionRecord.user_id == user.id))
            db.commit()
        return result


class AdminService:
    def __init__(self, factory, settings):
        self.factory = factory
        self.settings = settings

    def model_settings(self) -> ModelSettings:
        with self.factory() as db:
            row = db.get(AppSettingRecord, "models")
            if row is None:
                return ModelSettings(self.settings.openai_default_model, self.settings.allowed_models, 0)
            # Deployment allowlist remains the ceiling, even after a release removes a model.
            models = tuple(m for m in row.value["models"] if m in self.settings.allowed_models)
            if not models:
                raise ModelsUnavailable("No configured models remain available. Ask an admin to update model settings.")
            default = row.value["default"] if row.value["default"] in models else models[0]
            return ModelSettings(default, models, row.version)

    def save_models(self, principal, default: str, models: list[str], version: int, reason: str):
        if principal.role != "admin":
            raise PermissionError("Admin access required.")
        models = tuple(dict.fromkeys(models))
        if (not models or default not in models
                or any(m not in self.settings.allowed_models for m in models)):
            raise ValueError("Choose available models and select an enabled default model.")
        if not reason.strip() or len(reason) > 500:
            raise ValueError("Provide a reason between 1 and 500 characters.")
        value = {"default": default, "models": list(models)}
        try:
            with self.factory() as db:
                # Recheck within the write transaction; never trust form fields for identity or role.
                user = db.scalar(select(UserRecord).where(UserRecord.id == principal.id).with_for_update())
                if user is None or user.disabled or user.role != "admin":
                    raise PermissionError("Admin access required.")
                row = db.get(AppSettingRecord, "models")
                before = row.value if row else {
                    "default": self.settings.openai_default_model, "models": list(self.settings.allowed_models)}
                if version != (row.version if row else 0):
                    raise SettingsConflict("Settings changed. Reload this page before saving again.")
                if row is None:
                    db.add(AppSettingRecord(key="models", value=value, version=1))
                else:
                    changed = db.execute(update(AppSettingRecord).where(
                        AppSettingRecord.key == "models", AppSettingRecord.version == version
                    ).values(value=value, version=version + 1))
                    if changed.rowcount != 1:
                        raise SettingsConflict("Settings changed. Reload this page before saving again.")
                audit(db, principal.id, "models.change", "models", before, value, reason.strip())
                db.commit()
        except IntegrityError as exc:
            raise SettingsConflict("Settings changed. Reload this page before saving again.") from exc

    def recent_audit(self):
        with self.factory() as db:
            return list(db.scalars(select(AdminAuditRecord).order_by(
                AdminAuditRecord.created_at.desc(), AdminAuditRecord.id.desc()).limit(50)))
