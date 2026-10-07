"""Operator commands. Run inside the API container:

    python -m api.cli platform-admin grant you@example.com
    python -m api.cli platform-admin revoke you@example.com
    python -m api.cli platform-admin list

Platform admins may create (or, after deleting the stored credentials,
replace) the GitHub App that every organisation installs. Grants and
revocations are written to the user's organisation's audit log.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from db.audit import AuditAction, record_audit
from db.organization import User
from db.tenancy import bind_org
from sqlalchemy import select

from api.db.session import async_session_factory, engine


async def _platform_admin(command: str, email: str | None) -> int:
    async with async_session_factory() as session:
        if command == "list":
            admins = await session.scalars(
                select(User.email).where(User.is_platform_admin.is_(True)).order_by(User.email)
            )
            for admin_email in admins:
                print(admin_email)
            return 0

        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            print(f"no user with email {email!r}", file=sys.stderr)
            return 1
        grant = command == "grant"
        if user.is_platform_admin == grant:
            print(f"{user.email} is already {'a' if grant else 'not a'} platform admin")
            return 0
        user.is_platform_admin = grant
        await bind_org(session, user.org_id)
        record_audit(
            session,
            org_id=user.org_id,
            action=(
                AuditAction.PLATFORM_ADMIN_GRANTED if grant else AuditAction.PLATFORM_ADMIN_REVOKED
            ),
            target=f"user:{user.email}",
            metadata={"source": "cli"},
        )
        await session.commit()
        print(f"{'granted' if grant else 'revoked'} platform admin for {user.email}")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api.cli")
    sub = parser.add_subparsers(dest="group", required=True)
    admin = sub.add_parser("platform-admin", help="manage platform admins")
    admin.add_argument("command", choices=["grant", "revoke", "list"])
    admin.add_argument("email", nargs="?")
    args = parser.parse_args(argv)
    if args.command != "list" and not args.email:
        parser.error("an email is required for grant/revoke")

    async def run() -> int:
        try:
            return await _platform_admin(args.command, args.email)
        finally:
            await engine.dispose()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
