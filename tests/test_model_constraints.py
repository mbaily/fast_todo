import pytest
import uuid
from sqlmodel import select
from sqlalchemy.exc import IntegrityError
from app.db import async_session
from app.models import ListState, CompletionType, Hashtag, ListHashtag, Todo, TodoCompletion

pytestmark = pytest.mark.asyncio


async def unique_name(prefix: str = "n") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def test_completiontype_unique_constraint():
    name = await unique_name('ct')
    async with async_session() as sess:
        # create a list
        lst = ListState(name=await unique_name('list'))
        sess.add(lst)
        await sess.commit()
        await sess.refresh(lst)

        # create a completion type
        c = CompletionType(name=name, list_id=lst.id)
        sess.add(c)
        await sess.commit()

        # attempt duplicate insertion should raise IntegrityError on commit
        c2 = CompletionType(name=name, list_id=lst.id)
        sess.add(c2)
        with pytest.raises(IntegrityError):
            await sess.commit()
        await sess.rollback()


async def test_hashtag_and_listlink_unique_and_pk():
    tag = '#' + uuid.uuid4().hex[:6]
    async with async_session() as sess:
        lst = ListState(name=await unique_name('list'))
        sess.add(lst)
        await sess.commit()
        await sess.refresh(lst)
        lst_id = lst.id

        # create hashtag
        h = Hashtag(tag=tag)
        sess.add(h)
        await sess.commit()
        await sess.refresh(h)
        h_id = h.id

        # duplicate hashtag insert should fail; do it in a fresh session to
        # avoid identity-map conflicts (which raise SAWarning) when adding a
        # second Python instance with the same PK/key in the same session.
        async with async_session() as sess2:
            h2 = Hashtag(tag=tag)
            sess2.add(h2)
            with pytest.raises(IntegrityError):
                await sess2.commit()
            await sess2.rollback()

        # create list-hashtag link
        link = ListHashtag(list_id=lst_id, hashtag_id=h_id)
        sess.add(link)
        await sess.commit()

        # duplicate link (same PK) should raise; do it in a fresh session to
        # avoid creating a second instance with the same identity in the same
        # session (which emits SAWarning).
        async with async_session() as sess3:
            link2 = ListHashtag(list_id=lst_id, hashtag_id=h_id)
            sess3.add(link2)
            with pytest.raises(IntegrityError):
                await sess3.commit()
            await sess3.rollback()


async def test_todocompletion_pk_and_relationship():
    async with async_session() as sess:
        lst = ListState(name=await unique_name('list'))
        sess.add(lst)
        await sess.commit()
        await sess.refresh(lst)

        todo = Todo(text='model-test', list_id=lst.id)
        sess.add(todo)
        await sess.commit()
        await sess.refresh(todo)
        todo_id = todo.id

        c = CompletionType(name=await unique_name('ctype'), list_id=lst.id)
        sess.add(c)
        await sess.commit()
        await sess.refresh(c)
        c_id = c.id

        # create completion
        tc = TodoCompletion(todo_id=todo_id, completion_type_id=c_id, done=False)
        sess.add(tc)
        await sess.commit()

        # inserting duplicate TodoCompletion should violate PK; perform this
        # in a fresh session to avoid an identity-map conflict warning.
        async with async_session() as sess3:
            tc2 = TodoCompletion(todo_id=todo_id, completion_type_id=c_id, done=True)
            sess3.add(tc2)
            with pytest.raises(IntegrityError):
                await sess3.commit()
            await sess3.rollback()

        # ensure join query returns the created completion (use a fresh session
        # to avoid identity-map conflicts after the rollback)
        async with async_session() as sess2:
            q = await sess2.exec(select(TodoCompletion).where(TodoCompletion.todo_id == todo_id))
            rows = q.all()
            assert any(r.completion_type_id == c_id for r in rows)
