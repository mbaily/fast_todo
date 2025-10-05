import argparse
import asyncio
import json
from datetime import datetime

from sqlmodel import select

from app.db import async_session
from app.models import ListState, Todo, CompletionType, User
from app.utils import now_utc
from app.auth import pwd_context


async def ensure_user_password(sess, user: User, password: str | None) -> None:
    if not password:
        return
    try:
        hashed = pwd_context.hash(password)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"failed to hash password: {exc}") from exc
    user.password_hash = hashed
    sess.add(user)
    await sess.commit()
    await sess.refresh(user)


async def create_list(sess, user: User, name: str) -> ListState:
    lst = ListState(name=name, owner_id=user.id, created_at=now_utc(), modified_at=now_utc())
    sess.add(lst)
    await sess.commit()
    await sess.refresh(lst)
    # ensure default completion type exists
    qc = await sess.exec(
        select(CompletionType).where(CompletionType.list_id == lst.id).where(CompletionType.name == "default")
    )
    if not qc.first():
        sess.add(CompletionType(name="default", list_id=lst.id))
        await sess.commit()
    return lst


async def create_todo(
    sess,
    list_id: int,
    text: str,
    rrule: str,
    dtstart: datetime,
    note: str | None = None,
) -> Todo:
    todo = Todo(
        text=text,
        note=note,
        list_id=list_id,
        recurrence_rrule=rrule,
        recurrence_dtstart=dtstart,
        created_at=now_utc(),
        modified_at=now_utc(),
    )
    sess.add(todo)
    await sess.commit()
    await sess.refresh(todo)
    return todo


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed list/todo for ignore-from Playwright tests")
    parser.add_argument("--username", default="mbaily", help="User account to own the seeded data")
    parser.add_argument("--password", default=None, help="Optional password to set for the user")
    parser.add_argument("--list-name", required=True, help="Name of the list to create")
    parser.add_argument("--todo-text", required=True, help="Text of the recurring todo")
    parser.add_argument(
        "--rrule",
        default="FREQ=DAILY;INTERVAL=5",
        help="Recurrence RRULE string (default: FREQ=DAILY;INTERVAL=5)",
    )
    parser.add_argument(
        "--dtstart",
        default="2025-10-05T00:00:00+00:00",
        help="Recurrence DTSTART in ISO format (default: 2025-10-05T00:00:00+00:00)",
    )
    parser.add_argument("--note", default=None, help="Optional note for the todo")
    args = parser.parse_args()

    try:
        dtstart = datetime.fromisoformat(args.dtstart)
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"Invalid dtstart: {exc}")

    async with async_session() as sess:
        res = await sess.exec(select(User).where(User.username == args.username))
        user = res.first()
        if not user:
            if not args.password:
                raise SystemExit(
                    f"User {args.username!r} not found and no password provided to create it"
                )
            hashed = pwd_context.hash(args.password)
            user = User(username=args.username, password_hash=hashed, is_admin=True)
            sess.add(user)
            await sess.commit()
            await sess.refresh(user)

        if args.password:
            await ensure_user_password(sess, user, args.password)

        lst = await create_list(sess, user, args.list_name)
        todo = await create_todo(sess, lst.id, args.todo_text, args.rrule, dtstart, note=args.note)

    print(json.dumps({"list_id": lst.id, "todo_id": todo.id, "username": args.username}))


if __name__ == "__main__":
    asyncio.run(main())
