"""Admin commands.

    python -m app.cli create-manager --email you@example.com --name "اسمك"

The password is read from the BUD_ADMIN_PASSWORD environment variable, or prompted for.
Running it for an existing email promotes that employee to manager and resets the password.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from app.core.database import get_session
from app.core.security import hash_password, password_problem
from app.insurance.models import InsuranceEmployeeRole, SalesEmployee


async def create_manager(email: str, name: str, password: str) -> str:
    async with get_session() as session:
        result = await session.execute(select(SalesEmployee).where(SalesEmployee.email == email))
        employee = result.scalar_one_or_none()
        action = "updated"
        if employee is None:
            employee = SalesEmployee(email=email, full_name=name)
            session.add(employee)
            action = "created"
        employee.role = InsuranceEmployeeRole.MANAGER.value
        employee.is_active = True
        employee.password_hash = hash_password(password)
        await session.commit()
        return action


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    cm = sub.add_parser("create-manager", help="create or promote a manager account")
    cm.add_argument("--email", required=True)
    cm.add_argument("--name", required=True)
    args = parser.parse_args(argv)

    password = os.environ.get("BUD_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    if problem := password_problem(password):
        print(problem, file=sys.stderr)
        return 1
    action = asyncio.run(create_manager(args.email.strip().lower(), args.name.strip(), password))
    print(f"Manager {args.email} {action}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
