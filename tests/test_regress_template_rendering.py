import pytest
from sqlmodel import select


@pytest.mark.asyncio
async def test_list_and_todo_pages_render(client, ensure_db):
    # create a user and a list and a todo, then request the list and todo pages
    from app.db import async_session
    async with async_session() as sess:
        # reuse existing testuser from fixtures to avoid unique-constraint issues
        from app.models import User, ListState, Todo
        q = await sess.exec(select(User).where(User.username == 'testuser'))
        u = q.first()
        assert u is not None
        # create list owned by testuser
        lst = ListState(name='regress list', owner_id=u.id)
        sess.add(lst)
        await sess.commit()
        await sess.refresh(lst)
        # create a todo in the list
        t = Todo(text='regress todo', list_id=lst.id)
        sess.add(t)
        await sess.commit()
        await sess.refresh(t)

    # request list page (client is already authenticated as testuser via fixture)
    resp = await client.get(f'/html_no_js/lists/{lst.id}')
    assert resp.status_code == 200
    # request todo page
    resp = await client.get(f'/html_no_js/todos/{t.id}')
    assert resp.status_code == 200
