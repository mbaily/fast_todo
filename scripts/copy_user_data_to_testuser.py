#!/usr/bin/env python3
"""
Copy all data owned by a given source user (username 'mbaily') to a fresh
test user 'testuser'. This script is conservative by default (dry-run).

Usage:
  python scripts/copy_user_data_to_testuser.py --commit

Options:
  --commit    Actually write changes to the DB. If omitted the script will
              only print what it would do.

Notes:
  - The script copies categories, lists, todos, hashtags links, completion
    types/completions, item links owned by the user, and several per-user
    rows (collations, prefs, visits, occurrences, ignored scopes, ssh keys,
    push subscriptions, sync operations).
  - Sessions are not copied.
  - Hashtag rows are reused (not duplicated) because they are global and
    unique by tag.
"""
import argparse
import asyncio
import logging
from typing import Dict, List

from sqlmodel import select

from app.db import async_session
from app.auth import pwd_context
from app.models import (
    User,
    Category,
    ListState,
    Todo,
    ListHashtag,
    TodoHashtag,
    CompletionType,
    TodoCompletion,
    ItemLink,
    UserCollation,
    UserListPrefs,
    RecentListVisit,
    RecentTodoVisit,
    CompletedOccurrence,
    IgnoredScope,
    SshPublicKey,
    PushSubscription,
    SyncOperation,
    Session,
)

logger = logging.getLogger("copy_user_data")
logging.basicConfig(level=logging.INFO)


async def run(dry_run: bool = True, provided_password_hash: str | None = None):
    async with async_session() as sess:
        # Lookup source and target users
        q = await sess.exec(select(User).where(User.username == 'mbaily'))
        src = q.first()
        if not src:
            logger.error("source user 'mbaily' not found")
            return
        q2 = await sess.exec(select(User).where(User.username == 'testuser'))
        tgt = q2.first()

        # If target exists, delete it and related rows (only when commit)
        if tgt:
            logger.info("found existing testuser id=%s", tgt.id)
            if dry_run:
                logger.info("dry-run: would delete existing testuser and owned rows (user id=%s)", tgt.id)
            else:
                logger.info("deleting existing testuser rows and user row id=%s", tgt.id)
                # delete rows referencing this user id across known tables
                # Sessions: use SQLAlchemy delete() construct
                try:
                    from sqlalchemy import delete as sq_delete
                    await sess.exec(sq_delete(Session).where(Session.user_id == tgt.id))
                except Exception:
                    logger.exception("failed to delete sessions for target user %s", tgt.id)
                # Other per-user tables
                from sqlalchemy import delete as sq_delete
                for model, col in [
                    (ItemLink, ItemLink.owner_id),
                    (UserCollation, UserCollation.user_id),
                    (UserListPrefs, UserListPrefs.user_id),
                    (RecentListVisit, RecentListVisit.user_id),
                    (RecentTodoVisit, RecentTodoVisit.user_id),
                    (CompletedOccurrence, CompletedOccurrence.user_id),
                    (IgnoredScope, IgnoredScope.user_id),
                    (SshPublicKey, SshPublicKey.user_id),
                    (PushSubscription, PushSubscription.user_id),
                    (SyncOperation, SyncOperation.user_id),
                ]:
                    try:
                        await sess.exec(sq_delete(model).where(col == tgt.id))
                    except Exception:
                        logger.exception("failed to delete rows for model %s", model)
                # Delete lists owned by target (and cascading todos/completions handled below)
                try:
                    await sess.exec(sq_delete(ListState).where(ListState.owner_id == tgt.id))
                except Exception:
                    logger.exception("failed to delete target lists")
                # Finally delete the user row
                try:
                    await sess.exec(sq_delete(User).where(User.id == tgt.id))
                    await sess.commit()
                except Exception:
                    logger.exception("failed to delete target user row")

        # Create target user (copy password hash and flags from source)
        if dry_run:
            logger.info("dry-run: would create testuser copying from mbaily (password hash etc)")
        else:
            # use provided password if present (cli overrides), else copy source password_hash
            pw_hash = provided_password_hash if provided_password_hash is not None else src.password_hash
            new_user = User(username='testuser', password_hash=pw_hash, is_admin=False)
            sess.add(new_user)
            await sess.commit()
            await sess.refresh(new_user)
            tgt = new_user
            logger.info("created testuser id=%s", tgt.id)

        # For the rest of the operations we need the numeric ids
        src_id = src.id
        tgt_id = tgt.id if tgt else None
        # Map trackers
        category_map: Dict[int, int] = {}
        list_map: Dict[int, int] = {}
        todo_map: Dict[int, int] = {}
        completiontype_map: Dict[int, int] = {}

        # 1) Copy categories owned by src
        qcat = await sess.exec(select(Category).where(Category.owner_id == src_id))
        cat_rows = qcat.all()
        logger.info("found %d categories to copy", len(cat_rows))
        if not dry_run:
            for c in cat_rows:
                nc = Category(name=c.name, position=c.position, sort_alphanumeric=c.sort_alphanumeric, owner_id=tgt_id)
                sess.add(nc)
            await sess.commit()
            # refresh map
            qnew = await sess.exec(select(Category).where(Category.owner_id == tgt_id))
            for nc in qnew.all():
                # naive match by name+position (assumes no duplicates)
                for c in cat_rows:
                    if c.name == nc.name and c.position == nc.position:
                        category_map[c.id] = nc.id
                        break

        # 2) Copy lists owned by src (two-phase to handle parent refs)
        ql = await sess.exec(select(ListState).where(ListState.owner_id == src_id))
        lists = ql.all()
        logger.info("found %d lists to copy", len(lists))
        # First pass: create new list rows without parent refs
        if not dry_run and lists:
            for l in lists:
                nl = ListState(
                    name=l.name,
                    owner_id=tgt_id,
                    created_at=l.created_at,
                    modified_at=l.modified_at,
                    expanded=l.expanded,
                    hide_done=l.hide_done,
                    lists_up_top=l.lists_up_top,
                    hide_icons=l.hide_icons,
                    completed=l.completed,
                    category_id=(category_map.get(l.category_id) if l.category_id else None),
                    parent_todo_id=None,
                    parent_todo_position=l.parent_todo_position,
                    parent_list_id=None,
                    parent_list_position=l.parent_list_position,
                    priority=l.priority,
                )
                sess.add(nl)
            await sess.commit()
            # build list_map by matching names & created_at as a heuristic
            qnewlists = await sess.exec(select(ListState).where(ListState.owner_id == tgt_id))
            newlists = qnewlists.all()
            for nl in newlists:
                for l in lists:
                    if l.name == nl.name and l.created_at == nl.created_at:
                        list_map[l.id] = nl.id
                        break

        # 3) Copy todos belonging to the copied lists
        old_list_ids = [l.id for l in lists]
        if old_list_ids:
            qtodos = await sess.exec(select(Todo).where(Todo.list_id.in_(old_list_ids)))
            todos = qtodos.all()
        else:
            todos = []
        logger.info("found %d todos to copy", len(todos))
        if not dry_run and todos:
            for t in todos:
                new_list_id = list_map.get(t.list_id)
                if not new_list_id:
                    # skip any todo whose parent list wasn't copied
                    continue
                nt = Todo(
                    text=t.text,
                    note=t.note,
                    pinned=t.pinned,
                    search_ignored=t.search_ignored,
                    calendar_ignored=t.calendar_ignored,
                    created_at=t.created_at,
                    modified_at=t.modified_at,
                    deferred_until=t.deferred_until,
                    recurrence_rrule=t.recurrence_rrule,
                    recurrence_meta=t.recurrence_meta,
                    recurrence_dtstart=t.recurrence_dtstart,
                    recurrence_parser_version=t.recurrence_parser_version,
                    list_id=new_list_id,
                    priority=t.priority,
                    lists_up_top=t.lists_up_top,
                    sort_links=t.sort_links,
                )
                sess.add(nt)
            await sess.commit()
            # build todo map
            qnewtodos = await sess.exec(select(Todo).where(Todo.list_id.in_(list_map.values())))
            for nt in qnewtodos.all():
                # match by text+created_at as heuristic
                for t in todos:
                    if t.text == nt.text and t.created_at == nt.created_at:
                        todo_map[t.id] = nt.id
                        break

        # 4) Update parent refs on new lists (parent_list_id and parent_todo_id)
        if not dry_run and lists:
            # reload target lists into a dict by their new id
            for l in lists:
                new_id = list_map.get(l.id)
                if not new_id:
                    continue
                nl = await sess.get(ListState, new_id)
                # parent_list_id mapping
                if l.parent_list_id:
                    nl.parent_list_id = list_map.get(int(l.parent_list_id))
                if l.parent_todo_id:
                    nl.parent_todo_id = todo_map.get(int(l.parent_todo_id))
                nl.parent_list_position = l.parent_list_position
                nl.parent_todo_position = l.parent_todo_position
                sess.add(nl)
            await sess.commit()

        # 5) Copy ListHashtag and TodoHashtag entries
        if not dry_run:
            from sqlalchemy import insert as sq_insert
            # list hashtags
            qlh = await sess.exec(select(ListHashtag).where(ListHashtag.list_id.in_(old_list_ids)))
            lhas = qlh.all()
            for lh in lhas:
                new_list = list_map.get(lh.list_id)
                if not new_list:
                    continue
                # avoid duplicates
                exists_q = await sess.exec(select(ListHashtag).where(ListHashtag.list_id == new_list).where(ListHashtag.hashtag_id == lh.hashtag_id))
                if exists_q.first():
                    continue
                nl = ListHashtag(list_id=new_list, hashtag_id=lh.hashtag_id)
                sess.add(nl)
            # todo hashtags
            old_todo_ids = [t.id for t in todos]
            qth = await sess.exec(select(TodoHashtag).where(TodoHashtag.todo_id.in_(old_todo_ids)))
            ths = qth.all()
            for th in ths:
                new_todo = todo_map.get(th.todo_id)
                if not new_todo:
                    continue
                # avoid duplicates
                exists_q2 = await sess.exec(select(TodoHashtag).where(TodoHashtag.todo_id == new_todo).where(TodoHashtag.hashtag_id == th.hashtag_id))
                if exists_q2.first():
                    continue
                nt = TodoHashtag(todo_id=new_todo, hashtag_id=th.hashtag_id)
                sess.add(nt)
            await sess.commit()

        # 6) Copy CompletionType and TodoCompletion
        if not dry_run:
            qct = await sess.exec(select(CompletionType).where(CompletionType.list_id.in_(old_list_ids)))
            cts = qct.all()
            for ct in cts:
                nlid = list_map.get(ct.list_id)
                if not nlid:
                    continue
                # skip if same completion type name already exists on new list
                exists_ct = await sess.exec(select(CompletionType).where(CompletionType.list_id == nlid).where(CompletionType.name == ct.name))
                if exists_ct.first():
                    continue
                nct = CompletionType(name=ct.name, list_id=nlid)
                sess.add(nct)
            await sess.commit()
            # map completion types
            qnewcts = await sess.exec(select(CompletionType).where(CompletionType.list_id.in_(list_map.values())))
            for nct in qnewcts.all():
                for ct in cts:
                    if ct.name == nct.name and list_map.get(ct.list_id) == nct.list_id:
                        completiontype_map[ct.id] = nct.id
                        break
            # copy todo completions
            qtc = await sess.exec(select(TodoCompletion).where(TodoCompletion.todo_id.in_(old_todo_ids)))
            tcomps = qtc.all()
            for tc in tcomps:
                new_todo_id = todo_map.get(tc.todo_id)
                new_ct_id = completiontype_map.get(tc.completion_type_id)
                if not new_todo_id or not new_ct_id:
                    continue
                ntc = TodoCompletion(todo_id=new_todo_id, completion_type_id=new_ct_id, done=tc.done)
                sess.add(ntc)
            await sess.commit()

        # 7) Copy ItemLink rows owned by source user
        qil = await sess.exec(select(ItemLink).where(ItemLink.owner_id == src_id))
        ilinks = qil.all()
        logger.info("found %d itemlinks to copy (owner_id=%s)", len(ilinks), src_id)
        if not dry_run:
            for il in ilinks:
                src_type = il.src_type
                tgt_type = il.tgt_type
                new_src_id = il.src_id
                new_tgt_id = il.tgt_id
                # remap ids if they point to copied lists/todos
                if src_type == 'list' and il.src_id in list_map:
                    new_src_id = list_map[il.src_id]
                if src_type == 'todo' and il.src_id in todo_map:
                    new_src_id = todo_map[il.src_id]
                if tgt_type == 'list' and il.tgt_id in list_map:
                    new_tgt_id = list_map[il.tgt_id]
                if tgt_type == 'todo' and il.tgt_id in todo_map:
                    new_tgt_id = todo_map[il.tgt_id]
                # avoid creating duplicate edge (unique on src_type, src_id, tgt_type, tgt_id)
                exists_il = await sess.exec(select(ItemLink).where(ItemLink.src_type == src_type).where(ItemLink.src_id == new_src_id).where(ItemLink.tgt_type == tgt_type).where(ItemLink.tgt_id == new_tgt_id))
                if exists_il.first():
                    continue
                ni = ItemLink(src_type=src_type, src_id=new_src_id, tgt_type=tgt_type, tgt_id=new_tgt_id, label=il.label, position=il.position, owner_id=tgt_id)
                sess.add(ni)
            await sess.commit()

        # 8) Copy per-user tables (collations, prefs, visits, occurrences, ignored scopes)
        # Helper to copy simple rows by replacing user_id and mapping ids when necessary
        def _map_id(val: str, typ: str):
            try:
                iv = int(val)
            except Exception:
                return val
            if typ == 'list' and iv in list_map:
                return list_map[iv]
            if typ == 'todo' and iv in todo_map:
                return todo_map[iv]
            return iv

        if not dry_run:
            # UserCollation
            quc = await sess.exec(select(UserCollation).where(UserCollation.user_id == src_id))
            for r in quc.all():
                nlid = list_map.get(r.list_id) if r.list_id else None
                if nlid is None:
                    continue
                sess.add(UserCollation(user_id=tgt_id, list_id=nlid, active=r.active))
            # UserListPrefs
            qulp = await sess.exec(select(UserListPrefs).where(UserListPrefs.user_id == src_id))
            for r in qulp.all():
                nlid = list_map.get(r.list_id) if r.list_id else None
                if nlid is None:
                    continue
                sess.add(UserListPrefs(user_id=tgt_id, list_id=nlid, completed_after=r.completed_after))
            # RecentListVisit
            qrl = await sess.exec(select(RecentListVisit).where(RecentListVisit.user_id == src_id))
            for r in qrl.all():
                nlid = list_map.get(r.list_id) if r.list_id else None
                if nlid is None:
                    continue
                sess.add(RecentListVisit(user_id=tgt_id, list_id=nlid, visited_at=r.visited_at, position=r.position))
            # RecentTodoVisit
            qrt = await sess.exec(select(RecentTodoVisit).where(RecentTodoVisit.user_id == src_id))
            for r in qrt.all():
                ntid = todo_map.get(r.todo_id) if r.todo_id else None
                if ntid is None:
                    continue
                sess.add(RecentTodoVisit(user_id=tgt_id, todo_id=ntid, visited_at=r.visited_at, position=r.position))
            # CompletedOccurrence
            qco = await sess.exec(select(CompletedOccurrence).where(CompletedOccurrence.user_id == src_id))
            for r in qco.all():
                nid = r.item_id
                if r.item_type == 'list' and r.item_id in list_map:
                    nid = list_map[r.item_id]
                if r.item_type == 'todo' and r.item_id in todo_map:
                    nid = todo_map[r.item_id]
                sess.add(CompletedOccurrence(user_id=tgt_id, occ_hash=r.occ_hash, item_type=r.item_type, item_id=nid, occurrence_dt=r.occurrence_dt, completed_at=r.completed_at))
            # IgnoredScope
            qis = await sess.exec(select(IgnoredScope).where(IgnoredScope.user_id == src_id))
            for r in qis.all():
                sk = r.scope_key
                if r.scope_type == 'list':
                    try:
                        ik = int(sk)
                        skm = list_map.get(ik)
                        if skm is None:
                            continue
                        sk = str(skm)
                    except Exception:
                        pass
                if r.scope_type == 'todo':
                    try:
                        ik = int(sk)
                        skm = todo_map.get(ik)
                        if skm is None:
                            continue
                        sk = str(skm)
                    except Exception:
                        pass
                sess.add(IgnoredScope(user_id=tgt_id, scope_type=r.scope_type, scope_key=sk, from_dt=r.from_dt, scope_hash=r.scope_hash, created_at=r.created_at, active=r.active))
            # SshPublicKey
            qsk = await sess.exec(select(SshPublicKey).where(SshPublicKey.user_id == src_id))
            for r in qsk.all():
                sess.add(SshPublicKey(user_id=tgt_id, public_key=r.public_key, comment=r.comment, fingerprint=r.fingerprint, enabled=r.enabled, created_at=r.created_at))
            # PushSubscription
            qps = await sess.exec(select(PushSubscription).where(PushSubscription.user_id == src_id))
            for r in qps.all():
                sess.add(PushSubscription(user_id=tgt_id, subscription_json=r.subscription_json, enabled=r.enabled, created_at=r.created_at))
            # SyncOperation
            qso = await sess.exec(select(SyncOperation).where(SyncOperation.user_id == src_id))
            for r in qso.all():
                sess.add(SyncOperation(user_id=tgt_id, op_id=r.op_id, op_name=r.op_name, client_id=r.client_id, server_id=r.server_id, result_json=r.result_json, created_at=r.created_at))
            await sess.commit()

        # 9) Update testuser default_category_id and collation_list_id if present
        if not dry_run and src.default_category_id:
            new_def_cat = category_map.get(src.default_category_id)
            if new_def_cat:
                tgt_row = await sess.get(User, tgt_id)
                tgt_row.default_category_id = new_def_cat
                sess.add(tgt_row)
                await sess.commit()
        if not dry_run and src.collation_list_id:
            new_coll = list_map.get(src.collation_list_id)
            if new_coll:
                tgt_row = await sess.get(User, tgt_id)
                tgt_row.collation_list_id = new_coll
                sess.add(tgt_row)
                await sess.commit()

        logger.info("done (dry_run=%s). created mappings: categories=%d lists=%d todos=%d completiontypes=%d", dry_run, len(category_map), len(list_map), len(todo_map), len(completiontype_map))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--commit', action='store_true', help='Apply changes to the database')
    parser.add_argument('--password', type=str, default='testpass', help="Password for testuser (default: 'testpass')")
    args = parser.parse_args()
    dry_run = not args.commit
    # Hash the provided password using the app's pwd_context
    provided_hash = None
    if args.password:
        provided_hash = pwd_context.hash(args.password)
    asyncio.run(run(dry_run=dry_run, provided_password_hash=provided_hash))


if __name__ == '__main__':
    main()
