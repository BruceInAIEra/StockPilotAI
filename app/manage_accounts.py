"""Operator-only maintenance; never exposed as an HTTP endpoint."""
import argparse

from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.repositories.database import AnalysisRecord, UserRecord, create_session_factory


def assign_legacy(factory, user_id: str, apply: bool = False) -> int:
    with factory() as db:
        user = db.get(UserRecord, user_id)
        if user is None or user.disabled:
            raise ValueError("Target user must exist and be active; sign in first.")
        count = db.scalar(select(func.count()).select_from(AnalysisRecord).where(
            AnalysisRecord.user_id.is_(None)))
        if apply:
            db.execute(update(AnalysisRecord).where(AnalysisRecord.user_id.is_(None)).values(user_id=user_id))
            db.commit()
        return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list-users", "assign-legacy"])
    parser.add_argument("--user-id")
    parser.add_argument("--apply", action="store_true", help="Commit reassignment; otherwise only count records")
    args = parser.parse_args()
    factory = create_session_factory(get_settings().database_url)
    try:
        if args.command == "list-users":
            with factory() as db:
                for user in db.scalars(select(UserRecord).order_by(UserRecord.created_at)):
                    print(f"{user.id}\t{user.email}\t{'disabled' if user.disabled else 'active'}")
        else:
            if not args.user_id:
                parser.error("assign-legacy requires --user-id")
            count = assign_legacy(factory, args.user_id, args.apply)
            print(f"{'Assigned' if args.apply else 'Would assign'} {count} unassigned analyses.")
    except ValueError as exc:
        parser.error(str(exc))
    finally:
        factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
