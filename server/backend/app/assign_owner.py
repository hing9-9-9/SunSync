"""One-time command: give the spaces that have no owner to one account.

Spaces made before accounts existed belong to nobody and appear in no list. Run this once on the
server after that person has logged in at least once:

    /opt/sunsync/.venv/bin/python -m app.assign_owner --email someone@example.com [--apply]

Without --apply it only reports what would change.
"""

import argparse

from sqlalchemy import update
from sqlmodel import Session, col, func, select

from app.db import engine, init_db
from app.models.auth import AuthUser
from app.models.scene import SceneRecord
from app.ui_scene import DeletedSpaceRecord


def assign_unowned(session: Session, email: str, apply: bool) -> int:
    user = session.exec(select(AuthUser).where(AuthUser.email == email)).first()
    if user is None:
        raise SystemExit(f"No account with this e-mail has logged in yet: {email}")
    unowned = col(SceneRecord.owner_id).is_(None)
    count = session.exec(select(func.count()).select_from(SceneRecord).where(unowned)).one()
    if apply:
        if count:
            session.execute(update(SceneRecord).where(unowned).values(owner_id=user.id))
        # ids deleted before accounts existed go along, so that person's devices drop their copies
        session.execute(
            update(DeletedSpaceRecord)
            .where(col(DeletedSpaceRecord.owner_id).is_(None))
            .values(owner_id=user.id)
        )
        session.commit()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--apply", action="store_true", help="write the change")
    args = parser.parse_args()
    init_db()
    with Session(engine) as session:
        count = assign_unowned(session, args.email, args.apply)
    print(
        f"{count} space(s) {'assigned to' if args.apply else 'would be assigned to'} {args.email}"
    )


if __name__ == "__main__":
    main()
