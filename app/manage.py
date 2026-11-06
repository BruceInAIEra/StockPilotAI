"""Trusted server-only administration. No HTTP endpoint can assign roles."""
import argparse
import getpass
import socket

from sqlalchemy import select

from app.core.config import get_settings
from app.repositories.database import UserRecord, create_session_factory
from app.services.admin_service import assign_role


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list-users", help="List stored identities; sign in once to create a record")
    for command in ("grant-admin", "revoke-admin"):
        child = commands.add_parser(command)
        child.add_argument("--issuer", required=True)
        child.add_argument("--subject", required=True)
        child.add_argument("--reason", required=True)
        child.add_argument("--apply", action="store_true", help="Commit; otherwise preview only")
    args = parser.parse_args(argv)
    settings = get_settings()
    factory = create_session_factory(settings.database_url)
    try:
        if args.command == "list-users":
            with factory() as db:
                for user in db.scalars(select(UserRecord).order_by(UserRecord.created_at)):
                    print(f"{user.id}\t{user.email}\t{user.role}\t"
                          f"{'disabled' if user.disabled else 'active'}\t{user.issuer}\t{user.subject}")
        else:
            if not settings.auth_configured or args.issuer != settings.cognito_issuer:
                parser.error("Issuer must exactly match this deployment's configured Cognito user pool.")
            result = assign_role(factory, args.issuer, args.subject,
                                 "admin" if args.command == "grant-admin" else "user",
                                 actor=f"operator:{getpass.getuser()}@{socket.gethostname()}"[:255],
                                 reason=args.reason, apply=args.apply)
            print(f"{'Applied' if args.apply else 'Preview only'}: {result['user_id']} "
                  f"({result['email']}): {result['before']} -> {result['after']}")
            if args.apply:
                print("Role changes invalidate existing sessions. Sign in again.")
            else:
                print("Check the identity, then repeat with --apply to commit.")
    except ValueError as exc:
        parser.error(str(exc))
    finally:
        factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
