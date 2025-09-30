#!/usr/bin/env python3
"""
Add sublists for testing hide done feature.
"""
import asyncio
import os
from sqlmodel import select
from app.db import async_session, init_db
from app.models import User, ListState, Todo
from app import auth as _auth

async def run():
    await init_db()
    async with async_session() as sess:
        res = await sess.exec(select(User).where(User.username == 'dev_user'))
        user = res.first()
        if not user:
            user = User(username='dev_user', password_hash=_auth.pwd_context.hash('dev'))
            sess.add(user)
            await sess.commit()
            await sess.refresh(user)

        # Find or create list 490
        res = await sess.exec(select(ListState).where(ListState.id == 490))
        list_490 = res.first()
        if not list_490:
            list_490 = ListState(id=490, name='Test List 490', owner_id=user.id, sublists_hide_done=True)
            sess.add(list_490)
            await sess.commit()
            await sess.refresh(list_490)
        else:
            list_490.sublists_hide_done = True
            sess.add(list_490)
            await sess.commit()

        # Find or create todo 571
        res = await sess.exec(select(Todo).where(Todo.id == 571))
        todo_571 = res.first()
        if not todo_571:
            # Need a list for the todo
            res = await sess.exec(select(ListState).where(ListState.owner_id == user.id).limit(1))
            parent_list = res.first()
            if not parent_list:
                parent_list = ListState(name='Parent for Todo 571', owner_id=user.id)
                sess.add(parent_list)
                await sess.commit()
                await sess.refresh(parent_list)
            todo_571 = Todo(id=571, text='Test Todo 571', list_id=parent_list.id, sublists_hide_done=True)
            sess.add(todo_571)
            await sess.commit()
            await sess.refresh(todo_571)
        else:
            todo_571.sublists_hide_done = True
            sess.add(todo_571)
            await sess.commit()

        # Add 4 sublists to list 490: 1 not done, 3 done
        sublists_490 = []
        for i in range(4):
            name = f'Sublist 490-{i+1}'
            completed = i > 0  # first not done, others done
            sl = ListState(name=name, owner_id=user.id, parent_list_id=490, completed=completed)
            sess.add(sl)
            sublists_490.append(sl)
        await sess.commit()
        for sl in sublists_490:
            await sess.refresh(sl)

        # Add 4 sublists to todo 571: 1 not done, 3 done
        sublists_571 = []
        for i in range(4):
            name = f'Sublist 571-{i+1}'
            completed = i > 0  # first not done, others done
            sl = ListState(name=name, owner_id=user.id, parent_todo_id=571, completed=completed)
            sess.add(sl)
            sublists_571.append(sl)
        await sess.commit()
        for sl in sublists_571:
            await sess.refresh(sl)

        print('Added test data for hide done feature.')

if __name__ == '__main__':
    asyncio.run(run())