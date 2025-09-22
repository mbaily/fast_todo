from typing import Optional
import logging
import sys
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from .db import async_session
from .models import ListState, ListHashtag, Hashtag, Todo, TodoHashtag, CompletionType, TodoCompletion, Category, UserCollation, ItemLink, JournalEntry
from .utils import format_in_timezone
from .auth import get_current_user as _gcu
from .utils import extract_hashtags, now_utc, parse_metadata_json, validate_metadata_for_storage
from sqlalchemy import select, func, or_, and_

# Use a client-scoped prefix to avoid colliding with other APIs. These endpoints
# are intended for web clients (generic web frontends), not a generic public API.
router = APIRouter(prefix='/client/json')
logger = logging.getLogger(__name__)
# Ensure this module emits INFO logs even if root is WARNING (common default)
try:
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
except Exception:
    try:
        logger.setLevel(logging.INFO)
    except Exception:
        pass

# Attach a stdout StreamHandler like app.main so messages appear in server.log/stdout
try:
    if not logger.handlers:
        _h = logging.StreamHandler(sys.stdout)
        _h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s:%(name)s: %(message)s'))
        logger.addHandler(_h)
except Exception:
    pass


@router.get('/search', response_class=JSONResponse)
async def client_search(request: Request):
    """JSON search API for web clients. Mirrors /html_no_js/search logic but returns JSON."""
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')

    qparam = request.query_params.get('q', '').strip()
    include_list_todos = str(request.query_params.get('include_list_todos', '')).lower() in ('1','true','yes','on')
    if 'exclude_completed' in request.query_params:
        exclude_completed = str(request.query_params.get('exclude_completed', '')).lower() in ('1','true','yes','on')
    else:
        exclude_completed = True
    results = {'lists': [], 'todos': []}
    if qparam:
        like = f"%{qparam}%"
        try:
            search_tags = extract_hashtags(qparam)
        except Exception:
            search_tags = []
        async with async_session() as sess:
            owner_id = current_user.id
            qlists = select(ListState).where(ListState.owner_id == owner_id).where(ListState.name.ilike(like))
            rlists = await sess.exec(qlists)
            lists_by_id: dict[int, ListState] = {l.id: l for l in rlists.all()}
            if search_tags:
                qlh = (
                    select(ListState)
                    .join(ListHashtag, ListHashtag.list_id == ListState.id)
                    .join(Hashtag, Hashtag.id == ListHashtag.hashtag_id)
                    .where(ListState.owner_id == owner_id)
                    .where(Hashtag.tag.in_(search_tags))
                )
                rlh = await sess.exec(qlh)
                for l in rlh.all():
                    lists_by_id.setdefault(l.id, l)
            results['lists'] = [
                {'id': l.id, 'name': l.name, 'completed': getattr(l, 'completed', False), 'metadata': parse_metadata_json(getattr(l, 'metadata_json', None))}
                for l in lists_by_id.values()
                if not (exclude_completed and getattr(l, 'completed', False))
            ]
            # todos
            qvis = select(ListState).where((ListState.owner_id == owner_id) | (ListState.owner_id == None))
            rvis = await sess.exec(qvis)
            vis_ids = [l.id for l in rvis.all()]
            todos_acc: dict[int, Todo] = {}
            if vis_ids:
                qtodos = (
                    select(Todo)
                    .where(Todo.list_id.in_(vis_ids))
                    .where((Todo.text.ilike(like)) | (Todo.note.ilike(like)))
                    .where(Todo.search_ignored == False)
                )
                for t in (await sess.exec(qtodos)).all():
                    todos_acc.setdefault(t.id, t)
                if search_tags:
                    qth = (
                        select(Todo)
                        .join(TodoHashtag, TodoHashtag.todo_id == Todo.id)
                        .join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id)
                        .where(Todo.list_id.in_(vis_ids))
                        .where(Hashtag.tag.in_(search_tags))
                        .where(Todo.search_ignored == False)
                    )
                    for t in (await sess.exec(qth)).all():
                        todos_acc.setdefault(t.id, t)
                if include_list_todos and lists_by_id:
                    list_ids_match = list(lists_by_id.keys())
                    qall = select(Todo).where(Todo.list_id.in_(list_ids_match)).where(Todo.search_ignored == False)
                    for t in (await sess.exec(qall)).all():
                        todos_acc.setdefault(t.id, t)
                lm = {l.id: l.name for l in (await sess.scalars(select(ListState).where(ListState.id.in_(vis_ids)))).all()}
                todo_list_ids = list({t.list_id for t in todos_acc.values()})
                default_ct_ids: dict[int, int] = {}
                if todo_list_ids:
                    qct = select(CompletionType).where(CompletionType.list_id.in_(todo_list_ids)).where(CompletionType.name == 'default')
                    for ct in (await sess.exec(qct)).all():
                        default_ct_ids[int(ct.list_id)] = int(ct.id)
                todo_ids = list(todos_acc.keys())
                completed_ids: set[int] = set()
                if todo_ids and default_ct_ids:
                    qdone = select(TodoCompletion.todo_id, TodoCompletion.done, TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(todo_ids)).where(TodoCompletion.completion_type_id.in_(list(default_ct_ids.values())))
                    for tid, done_val, ctid in (await sess.exec(qdone)).all():
                        if done_val:
                            completed_ids.add(int(tid))
                results['todos'] = [
                    {'id': t.id, 'text': t.text, 'note': t.note, 'list_id': t.list_id, 'list_name': lm.get(t.list_id), 'completed': (int(t.id) in completed_ids), 'metadata': parse_metadata_json(getattr(t, 'metadata_json', None))}
                    for t in todos_acc.values() if not (exclude_completed and (int(t.id) in completed_ids))
                ]
    return JSONResponse({'ok': True, 'q': qparam, 'results': results})


@router.post('/calcdict', response_class=JSONResponse)
async def client_calc_dict(request: Request):
    """Calculate using CalcDict: Body JSON { name?: str, input_text: str } -> { ok, output: str }.
    Follows required sequence: clear -> CalcDict(name).assn(input) -> total_up_all -> clear.
    """
    try:
        # Require authentication to scope any future per-user behaviors; can be relaxed if needed
        await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')
    try:
        body = await request.json()
    except Exception:
        body = {}
    input_text = (body.get('input_text') or '').strip()
    name = body.get('name') or 'note'
    if not input_text:
        try:
            logger.info('calcdict: empty input (name=%s)', name)
        except Exception:
            pass
        return JSONResponse({'ok': True, 'output': ''})
    # Execute CalcDict per instructions
    try:
        from .CalcDict import CalcDict, clear_calcdict_instances
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'CalcDict not available: {e}')
    try:
        try:
            logger.info('calcdict: input (name=%s, len=%d)\n%s', name, len(input_text), input_text)
        except Exception:
            pass
        clear_calcdict_instances()
        c = CalcDict(name)
        c.assn(input_text)
        output = CalcDict.total_up_all(print_total=False)
        clear_calcdict_instances()
        try:
            logger.info('calcdict: output (name=%s, len=%d)\n%s', name, len(output or ''), output or '')
        except Exception:
            pass
    except Exception as e:
        # Return error string in output while still indicating ok=false
        try:
            logger.exception('calcdict: error (name=%s): %s', name, e)
        except Exception:
            pass
        return JSONResponse({'ok': False, 'error': str(e)})
    return JSONResponse({'ok': True, 'output': output})


@router.post('/lists', response_class=JSONResponse)
async def client_create_list(request: Request):
    """Create a list via JSON for web clients. Reuses create_list logic.
    Accepts JSON body {name: string, hashtags?: [...], category_id?: int} and returns created list id/name.
    """
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')

    try:
        body = await request.json()
    except Exception:
        body = {}
    name = body.get('name') or request.query_params.get('name')
    if not name:
        raise HTTPException(status_code=400, detail='name is required')
    metadata = body.get('metadata') if isinstance(body, dict) else None
    # Delegate to existing create_list helper which expects Request and current_user
    # Use local import to avoid circular dependency
    from .main import create_list
    # Pass metadata via request state to avoid changing function signature for non-JSON callers
    try:
        request.state._list_metadata = metadata
    except Exception:
        pass
    new_list = await create_list(request, name=name, current_user=current_user)
    payload = {'ok': True}
    try:
        if new_list is not None:
            payload.update({'id': getattr(new_list, 'id', None), 'name': getattr(new_list, 'name', None), 'category_id': getattr(new_list, 'category_id', None), 'metadata': parse_metadata_json(getattr(new_list, 'metadata_json', None))})
    except Exception:
        pass
    return JSONResponse(payload)


# ===== Journal entries (per-todo) =====

def _safe_str(v):
    try:
        return str(v)
    except Exception:
        return ''

@router.get('/todos/{todo_id}/journal', response_class=JSONResponse)
async def list_journal_entries(request: Request, todo_id: int):
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')
    async with async_session() as sess:
        t = await sess.get(Todo, todo_id)
        if not t:
            raise HTTPException(status_code=404, detail='todo not found')
        # verify visibility via parent list ownership
        lst = await sess.get(ListState, t.list_id)
        if not lst or lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        q = await sess.scalars(
            select(JournalEntry)
            .where(JournalEntry.todo_id == todo_id)
            .order_by(JournalEntry.created_at.desc())
        )
        rows = q.all()
        # Optional timezone hint from client (IANA zone); when provided, include
        # pre-formatted display strings matching the rest of the html_no_js UI.
        tz = request.query_params.get('tz')
        display_fmt = '%d/%m %-I:%M%p'
        out = [
            {
                'id': r.id,
                'todo_id': r.todo_id,
                'content': r.content,
                'created_at': (r.created_at.isoformat() if getattr(r, 'created_at', None) else None),
                'modified_at': (r.modified_at.isoformat() if getattr(r, 'modified_at', None) else None),
                # display strings (when tz is given)
                'created_at_display': (format_in_timezone(r.created_at, tz, display_fmt) if (tz and getattr(r, 'created_at', None)) else None),
                'modified_at_display': (format_in_timezone(r.modified_at, tz, display_fmt) if (tz and getattr(r, 'modified_at', None)) else None),
                'metadata': parse_metadata_json(getattr(r, 'metadata_json', None)),
            }
            for r in rows
        ]
        return JSONResponse({'ok': True, 'entries': out})


@router.post('/todos/{todo_id}/journal', response_class=JSONResponse)
async def create_journal_entry(request: Request, todo_id: int):
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')
    try:
        body = await request.json()
    except Exception:
        body = {}
    content = _safe_str((body.get('content') or '').strip())
    if not content:
        raise HTTPException(status_code=400, detail='content is required')
    metadata = body.get('metadata') if isinstance(body, dict) else None
    async with async_session() as sess:
        t = await sess.get(Todo, todo_id)
        if not t:
            raise HTTPException(status_code=404, detail='todo not found')
        lst = await sess.get(ListState, t.list_id)
        if not lst or lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        # validate metadata
        meta_json = None
        try:
            meta_json = validate_metadata_for_storage(metadata)
        except Exception:
            meta_json = None
        row = JournalEntry(todo_id=todo_id, user_id=current_user.id, content=content, created_at=now_utc(), modified_at=now_utc(), metadata_json=meta_json)
        sess.add(row)
        await sess.commit()
        await sess.refresh(row)
        resp = {'ok': True, 'entry': {'id': row.id, 'todo_id': row.todo_id, 'content': row.content, 'created_at': (row.created_at.isoformat() if row.created_at else None), 'modified_at': (row.modified_at.isoformat() if row.modified_at else None)}}
        return JSONResponse(resp)


@router.patch('/journal/{entry_id}', response_class=JSONResponse)
async def update_journal_entry(request: Request, entry_id: int):
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')
    try:
        body = await request.json()
    except Exception:
        body = {}
    content = body.get('content')
    metadata = body.get('metadata') if isinstance(body, dict) else None
    async with async_session() as sess:
        r = await sess.get(JournalEntry, entry_id)
        if not r:
            raise HTTPException(status_code=404, detail='journal entry not found')
        # Verify ownership via user and list
        t = await sess.get(Todo, r.todo_id)
        if not t:
            raise HTTPException(status_code=404, detail='todo not found')
        lst = await sess.get(ListState, t.list_id)
        if not lst or lst.owner_id != current_user.id or r.user_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        changed = False
        if content is not None:
            r.content = _safe_str(content)
            changed = True
        if metadata is not None:
            try:
                r.metadata_json = validate_metadata_for_storage(metadata)
            except Exception:
                r.metadata_json = r.metadata_json
            changed = True
        if changed:
            r.modified_at = now_utc()
            sess.add(r)
            await sess.commit()
            await sess.refresh(r)
        return JSONResponse({'ok': True, 'entry': {'id': r.id, 'todo_id': r.todo_id, 'content': r.content, 'created_at': (r.created_at.isoformat() if r.created_at else None), 'modified_at': (r.modified_at.isoformat() if r.modified_at else None)}})


@router.delete('/journal/{entry_id}', response_class=JSONResponse)
async def delete_journal_entry(request: Request, entry_id: int):
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')
    async with async_session() as sess:
        r = await sess.get(JournalEntry, entry_id)
        if not r:
            return JSONResponse({'ok': True, 'deleted': False})
        # verify owner
        t = await sess.get(Todo, r.todo_id)
        if not t:
            raise HTTPException(status_code=404, detail='todo not found')
        lst = await sess.get(ListState, t.list_id)
        if not lst or lst.owner_id != current_user.id or r.user_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        await sess.delete(r)
        await sess.commit()
        return JSONResponse({'ok': True, 'deleted': True})


# ===== Collations (per-user multiple collection lists) =====

@router.get('/collations', response_class=JSONResponse)
async def list_user_collations(request: Request):
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')
    async with async_session() as sess:
        # Use scalars() to retrieve ORM instances rather than Row objects
        rows = (await sess.scalars(select(UserCollation).where(UserCollation.user_id == current_user.id))).all()
        # load list names
        list_ids = [r.list_id for r in rows]
        names = {}
        if list_ids:
            r2 = await sess.exec(select(ListState.id, ListState.name, ListState.owner_id).where(ListState.id.in_(list_ids)))
            for lid, name, owner_id in r2.all():
                # Only expose collations pointing to the user's own lists
                if owner_id == current_user.id:
                    names[int(lid)] = name
        # Exclude lists that are currently in Trash (by parent_list_id to the user's Trash list)
        trashed: set[int] = set()
        if list_ids:
            trash_id = None
            try:
                trq = await sess.scalars(select(ListState.id).where(ListState.owner_id == current_user.id).where(ListState.name == 'Trash'))
                trash_id = trq.first()
            except Exception:
                trash_id = None
            if trash_id is not None:
                tq = await sess.scalars(select(ListState.id).where(ListState.id.in_(list_ids)).where(ListState.parent_list_id == trash_id))
                trashed = set(int(v) for v in tq.all())
        out = [
            {'list_id': int(r.list_id), 'name': names.get(int(r.list_id)), 'active': bool(getattr(r, 'active', True))}
            for r in rows if (int(r.list_id) in names and int(r.list_id) not in trashed)
        ]
    return JSONResponse({'ok': True, 'collations': out})


@router.post('/collations', response_class=JSONResponse)
async def create_user_collation(request: Request):
    """Add a list to the user's set of collations. Body: {list_id:int, active?:bool}."""
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')
    try:
        body = await request.json()
    except Exception:
        body = {}
    list_id = body.get('list_id')
    if not list_id:
        raise HTTPException(status_code=400, detail='list_id required')
    active = bool(body.get('active', True))
    async with async_session() as sess:
        lst = await sess.get(ListState, int(list_id))
        if not lst or lst.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail='list not found')
        existing = await sess.get(UserCollation, (current_user.id, int(list_id)))
        if existing:
            existing.active = active
            sess.add(existing)
        else:
            sess.add(UserCollation(user_id=current_user.id, list_id=int(list_id), active=active))
        await sess.commit()
    return JSONResponse({'ok': True, 'list_id': int(list_id), 'active': active})


@router.post('/collations/{list_id}/active', response_class=JSONResponse)
async def set_collation_active(request: Request, list_id: int):
    """Set active flag for a user's collation list. Body: {active: bool}."""
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')
    try:
        body = await request.json()
    except Exception:
        body = {}
    active = bool(body.get('active', True))
    async with async_session() as sess:
        lst = await sess.get(ListState, list_id)
        if not lst or lst.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail='list not found')
        row = await sess.get(UserCollation, (current_user.id, list_id))
        if not row:
            row = UserCollation(user_id=current_user.id, list_id=list_id, active=active)
        else:
            row.active = active
        sess.add(row)
        await sess.commit()
    return JSONResponse({'ok': True, 'list_id': list_id, 'active': active})


@router.get('/collations/status', response_class=JSONResponse)
async def get_collation_status(request: Request, todo_id: int):
    """Return membership map for active collations for the current user.
    Response: { ok: true, memberships: [{list_id, name, linked}] }
    """
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')
    async with async_session() as sess:
        # collations
        rows = (await sess.scalars(select(UserCollation).where(UserCollation.user_id == current_user.id).where(UserCollation.active == True))).all()
        ids = [r.list_id for r in rows]
        names = {}
        if ids:
            r2 = await sess.exec(select(ListState.id, ListState.name).where(ListState.id.in_(ids)).where(ListState.owner_id == current_user.id))
            for lid, name in r2.all():
                names[int(lid)] = name
        # Filter out collation lists that are currently in Trash (parent to user's Trash list)
        trashed: set[int] = set()
        if ids:
            trash_id = None
            try:
                trq = await sess.scalars(select(ListState.id).where(ListState.owner_id == current_user.id).where(ListState.name == 'Trash'))
                trash_id = trq.first()
            except Exception:
                trash_id = None
            if trash_id is not None:
                tq = await sess.scalars(select(ListState.id).where(ListState.id.in_(ids)).where(ListState.parent_list_id == trash_id))
                trashed = set(int(v) for v in tq.all())
        linked_map = {}
        if ids:
            res = await sess.scalars(
                select(ItemLink.src_id)
                .where(ItemLink.src_type == 'list')
                .where(ItemLink.tgt_type == 'todo')
                .where(ItemLink.tgt_id == todo_id)
                .where(ItemLink.src_id.in_(ids))
            )
            for sid in res.all():
                try:
                    linked_map[int(sid)] = True
                except Exception:
                    pass
        out = []
        for r in rows:
            lid = int(r.list_id)
            if (lid not in names) or (lid in trashed):
                continue
            out.append({'list_id': lid, 'name': names.get(lid), 'linked': bool(linked_map.get(lid, False))})
    return JSONResponse({'ok': True, 'memberships': out})


@router.post('/collations/{list_id}/toggle', response_class=JSONResponse)
async def toggle_collation_membership(request: Request, list_id: int):
    """Toggle membership of a todo in a given collation. Body: {todo_id:int, link?:bool}.
    If link is omitted, it toggles. Returns {ok, linked}.
    """
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')
    try:
        body = await request.json()
    except Exception:
        body = {}
    todo_id = body.get('todo_id')
    link_req = body.get('link')
    if not todo_id:
        raise HTTPException(status_code=400, detail='todo_id required')
    async with async_session() as sess:
        # ensure list belongs to user and is a registered collation
        lst = await sess.get(ListState, list_id)
        if not lst or lst.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail='list not found')
        # Disallow toggling when the list is in Trash (parented to the user's Trash list)
        try:
            trq = await sess.scalars(select(ListState.id).where(ListState.owner_id == current_user.id).where(ListState.name == 'Trash'))
            trash_id = trq.first()
        except Exception:
            trash_id = None
        if trash_id is not None:
            pq = await sess.scalars(select(ListState.id).where(ListState.id == list_id).where(ListState.parent_list_id == trash_id))
            if pq.first() is not None:
                raise HTTPException(status_code=409, detail='list is trashed')
        uc = await sess.get(UserCollation, (current_user.id, list_id))
        if not uc:
            raise HTTPException(status_code=403, detail='not a user collation')
        # ensure todo is visible via ownership of its parent list
        td = await sess.get(Todo, int(todo_id))
        if not td:
            raise HTTPException(status_code=404, detail='todo not found')
        # Load parent list as an ORM object to access attributes safely
        ql = await sess.scalars(select(ListState).where(ListState.id == td.list_id))
        pl = ql.first()
        if not pl or pl.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        # check existing link
        q = await sess.scalars(
            select(ItemLink)
            .where(ItemLink.src_type == 'list')
            .where(ItemLink.src_id == list_id)
            .where(ItemLink.tgt_type == 'todo')
            .where(ItemLink.tgt_id == int(todo_id))
        )
        existing = q.first()
        # determine desired state
        want_link: bool
        if link_req is None:
            want_link = existing is None
        else:
            want_link = bool(link_req)
        if want_link and not existing:
            # compute next position
            try:
                res = await sess.exec(
                    select(func.max(ItemLink.position))
                    .where(ItemLink.src_type == 'list')
                    .where(ItemLink.src_id == list_id)
                )
                row = res.first()
                if row is None:
                    max_pos = None
                else:
                    try:
                        # row may be a Row or scalar depending on driver
                        max_pos = row[0] if isinstance(row, (tuple, list)) else (
                            row if isinstance(row, (int, type(None))) else getattr(row, '_mapping', {}).get('max_1')
                        )
                    except Exception:
                        max_pos = None
                pos = (int(max_pos) + 1) if (max_pos is not None) else 0
            except Exception:
                pos = 0
            sess.add(
                ItemLink(
                    src_type='list',
                    src_id=list_id,
                    tgt_type='todo',
                    tgt_id=int(todo_id),
                    position=pos,
                    owner_id=current_user.id,
                )
            )
            await sess.commit()
            return JSONResponse({'ok': True, 'linked': True})
        if (not want_link) and existing:
            await sess.delete(existing)
            await sess.commit()
            return JSONResponse({'ok': True, 'linked': False})
        # no change
        return JSONResponse({'ok': True, 'linked': existing is not None})


@router.delete('/collations/{list_id}', response_class=JSONResponse)
async def delete_user_collation(request: Request, list_id: int):
    """Unmark a list as a user collation (remove the UserCollation row)."""
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')
    async with async_session() as sess:
        lst = await sess.get(ListState, list_id)
        if not lst or lst.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail='list not found')
        row = await sess.get(UserCollation, (current_user.id, list_id))
        if not row:
            return JSONResponse({'ok': True, 'removed': False})
        # Optionally, do not cascade-delete ItemLinks; we only remove the collation registration.
        await sess.delete(row)
        await sess.commit()
    return JSONResponse({'ok': True, 'removed': True})


@router.get('/lists', response_class=JSONResponse)
async def client_list_index(request: Request, per_page: Optional[int] = None):
    """Return top-level lists (no parent) for the current user, grouped by category,
    with pagination cursors and pinned todos. Mirrors html_no_js index logic.
    Query params: dir ('next' or 'prev'), cursor_created_at (ISO), cursor_id (int), per_page
    """
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')

    # pagination defaults
    try:
        per_page_val = int(per_page) if per_page is not None else 50
    except Exception:
        per_page_val = 50
    dir_param = request.query_params.get('dir', 'next')
    cursor_created_at_str = request.query_params.get('cursor_created_at')
    cursor_id_str = request.query_params.get('cursor_id')
    cursor_dt = None
    cursor_id = None
    if cursor_created_at_str and cursor_id_str:
        try:
            cursor_dt = datetime.fromisoformat(cursor_created_at_str)
            cursor_id = int(cursor_id_str)
        except Exception:
            cursor_dt, cursor_id = None, None

    async with async_session() as sess:
        owner_id = current_user.id
        q = select(ListState).where(ListState.owner_id == owner_id).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None)
        if cursor_dt is not None and cursor_id is not None:
            if dir_param == 'prev':
                q = q.where(or_(ListState.created_at > cursor_dt, and_(ListState.created_at == cursor_dt, ListState.id > cursor_id)))
            else:
                q = q.where(or_(ListState.created_at < cursor_dt, and_(ListState.created_at == cursor_dt, ListState.id < cursor_id)))
        q = q.order_by(ListState.created_at.desc(), ListState.id.desc()).limit(per_page_val)
        res_page = await sess.exec(q)
        lists = res_page.all()

        has_prev = False
        has_next = False
        next_cursor_created_at = None
        next_cursor_id = None
        prev_cursor_created_at = None
        prev_cursor_id = None
        if lists:
            first = lists[0]
            last = lists[-1]
            prev_cursor_created_at, prev_cursor_id = first.created_at, first.id
            next_cursor_created_at, next_cursor_id = last.created_at, last.id
            q_prev_exists = select(ListState.id).where(ListState.owner_id == owner_id).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None).where(
                or_(ListState.created_at > first.created_at, and_(ListState.created_at == first.created_at, ListState.id > first.id))
            ).limit(1)
            r_prev = await sess.exec(q_prev_exists)
            has_prev = r_prev.first() is not None
            q_next_exists = select(ListState.id).where(ListState.owner_id == owner_id).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None).where(
                or_(ListState.created_at < last.created_at, and_(ListState.created_at == last.created_at, ListState.id < last.id))
            ).limit(1)
            r_next = await sess.exec(q_next_exists)
            has_next = r_next.first() is not None

        # convert ORM ListState objects to plain dicts
        list_rows = []
        list_ids = [l.id for l in lists]
        tag_map: dict[int, list[str]] = {}
        if list_ids:
            qlh = await sess.exec(select(ListHashtag.list_id, Hashtag.tag).where(ListHashtag.list_id.in_(list_ids)).join(Hashtag, Hashtag.id == ListHashtag.hashtag_id))
            rows = qlh.all()
            for lid, tag in rows:
                tag_map.setdefault(lid, []).append(tag)
        for l in lists:
            list_rows.append({
                'id': l.id,
                'name': l.name,
                'completed': l.completed,
                'owner_id': l.owner_id,
                'created_at': (l.created_at.isoformat() if getattr(l, 'created_at', None) else None),
                'modified_at': (getattr(l, 'modified_at', None).isoformat() if getattr(l, 'modified_at', None) else None),
                'category_id': l.category_id,
                'priority': getattr(l, 'priority', None),
                'override_priority': None,
                'hashtags': tag_map.get(l.id, []),
                'uncompleted_count': None,
                'hide_icons': getattr(l, 'hide_icons', False),
                'metadata': parse_metadata_json(getattr(l, 'metadata_json', None)),
            })

        # Determine highest uncompleted todo priority per list
        try:
            todo_q = await sess.scalars(select(Todo.id, Todo.list_id, Todo.priority).where(Todo.list_id.in_(list_ids)).where(Todo.priority != None))
            todo_rows = todo_q.all()
            todo_map: dict[int, list[tuple[int,int]]] = {}
            todo_ids = []
            for tid, lid, pri in todo_rows:
                todo_map.setdefault(lid, []).append((tid, pri))
                todo_ids.append(tid)
            completed_ids = set()
            if todo_ids:
                try:
                    qcomp = select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(todo_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True)
                    cres = await sess.exec(qcomp)
                    completed_ids = set(r[0] if isinstance(r, tuple) else r for r in cres.all())
                except Exception:
                    completed_ids = set()
            for row in list_rows:
                lid = row.get('id')
                candidates = todo_map.get(lid, [])
                max_p = None
                for tid, pri in candidates:
                    if tid in completed_ids:
                        continue
                    try:
                        if pri is None:
                            continue
                        pv = int(pri)
                    except Exception:
                        continue
                    if max_p is None or pv > max_p:
                        max_p = pv
                if max_p is not None:
                    row['override_priority'] = max_p
        except Exception:
            pass

        # Compute uncompleted counts per list (collation-aware)
        try:
            qcnt = await sess.exec(select(Todo.list_id, func.count(Todo.id)).where(Todo.list_id.in_(list_ids)).outerjoin(TodoCompletion, TodoCompletion.todo_id == Todo.id).group_by(Todo.list_id))
            counts = {}
            for lid, cnt in qcnt.all():
                counts[lid] = int(cnt or 0)
            try:
                qcomp = await sess.exec(select(Todo.id, Todo.list_id).join(TodoCompletion, TodoCompletion.todo_id == Todo.id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(Todo.list_id.in_(list_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True))
                for tid, lid in qcomp.all():
                    counts[lid] = max(0, counts.get(lid, 0) - 1)
            except Exception:
                pass

            extra_counts: dict[int, int] = {}
            try:
                quc = await sess.exec(select(UserCollation.list_id).where(UserCollation.user_id == owner_id))
                uc_ids_all = [r[0] if isinstance(r, (list, tuple)) else int(getattr(r, 'list_id', r)) for r in quc.all()]
                collation_ids = [lid for lid in uc_ids_all if lid in list_ids]
                if collation_ids:
                    qlinks = await sess.exec(
                        select(ItemLink.src_id, ItemLink.tgt_id)
                        .where(ItemLink.src_type == 'list')
                        .where(ItemLink.tgt_type == 'todo')
                        .where(ItemLink.src_id.in_(collation_ids))
                        .where(ItemLink.owner_id == owner_id)
                    )
                    link_rows = qlinks.all()
                    coll_link_map: dict[int, set[int]] = {}
                    all_linked_ids: set[int] = set()
                    for src_id, tgt_id in link_rows:
                        try:
                            sid = int(src_id); tid = int(tgt_id)
                        except Exception:
                            continue
                        coll_link_map.setdefault(sid, set()).add(tid)
                        all_linked_ids.add(tid)
                    if all_linked_ids:
                        try:
                            qlcomp = await sess.exec(select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(list(all_linked_ids))).where(CompletionType.name == 'default').where(TodoCompletion.done == True))
                            linked_completed = set(r[0] if isinstance(r, tuple) else r for r in qlcomp.all())
                        except Exception:
                            linked_completed = set()
                        qtl = await sess.exec(select(Todo.id, Todo.list_id).where(Todo.id.in_(list(all_linked_ids))))
                        todo_src_map: dict[int, int] = {int(tid): int(lid) for tid, lid in qtl.all()}
                        for lid, tids in coll_link_map.items():
                            extra = 0
                            for tid in set(tids):
                                if tid in linked_completed:
                                    continue
                                if todo_src_map.get(int(tid)) == int(lid):
                                    continue
                                extra += 1
                            if extra:
                                extra_counts[int(lid)] = extra
            except Exception:
                pass

            for row in list_rows:
                lid = row.get('id')
                row['uncompleted_count'] = counts.get(lid, 0) + extra_counts.get(lid, 0)
        except Exception:
            pass

        # group lists by category
        lists_by_category: dict[int, list[dict]] = {}
        for row in list_rows:
            cid = row.get('category_id') or 0
            lists_by_category.setdefault(cid, []).append(row)
        for cid, rows in lists_by_category.items():
            def _list_sort_key(r):
                lp = r.get('priority') if (r.get('priority') is not None and not r.get('completed')) else None
                op = r.get('override_priority') if (r.get('override_priority') is not None and not r.get('completed')) else None
                if lp is None and op is None:
                    p = None
                elif lp is None:
                    p = op
                elif op is None:
                    p = lp
                else:
                    p = lp if lp >= op else op
                return (0 if p is not None else 1, p or 0, -(datetime.fromisoformat(r.get('created_at')).timestamp() if r.get('created_at') else 0))
            rows.sort(key=_list_sort_key)

        # fetch categories (user-scoped)
        categories = []
        try:
            qcat = select(Category).where(Category.owner_id == current_user.id).order_by(Category.position.asc())
            cres = await sess.exec(qcat)
            categories = [{'id': c.id, 'name': c.name, 'position': c.position, 'sort_alphanumeric': getattr(c, 'sort_alphanumeric', False)} for c in cres.all()]
        except Exception:
            categories = []

        # pinned todos
        pinned_todos = []
        try:
            qvis = select(ListState).where(((ListState.owner_id == owner_id) | (ListState.owner_id == None))).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None)
            rvis = await sess.exec(qvis)
            vis_lists = rvis.all()
            vis_ids = [l.id for l in vis_lists]
            if vis_ids:
                qp = select(Todo).where(Todo.pinned == True).where(Todo.list_id.in_(vis_ids)).order_by(Todo.modified_at.desc())
                pres = await sess.exec(qp)
                pin_rows = pres.all()
                lm = {l.id: l.name for l in vis_lists}
                pinned_todos = [
                    {
                        'id': t.id,
                        'text': t.text,
                        'list_id': t.list_id,
                        'list_name': lm.get(t.list_id),
                        'modified_at': (t.modified_at.isoformat() if getattr(t, 'modified_at', None) else None),
                        'priority': getattr(t, 'priority', None),
                        'override_priority': getattr(t, 'override_priority', None) if hasattr(t, 'override_priority') else None,
                    }
                    for t in pin_rows
                ]
                pin_ids = [p['id'] for p in pinned_todos]
                if pin_ids:
                    qtp = select(TodoHashtag.todo_id, Hashtag.tag).join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id).where(TodoHashtag.todo_id.in_(pin_ids))
                    pres2 = await sess.exec(qtp)
                    pm = {}
                    for tid, tag in pres2.all():
                        pm.setdefault(tid, []).append(tag)
                    for p in pinned_todos:
                        p['tags'] = pm.get(p['id'], [])
                try:
                    if pin_ids:
                        qcomp = select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(pin_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True)
                        cres = await sess.exec(qcomp)
                        completed_ids = set(r[0] if isinstance(r, tuple) else r for r in cres.all())
                    else:
                        completed_ids = set()
                except Exception:
                    completed_ids = set()
                for p in pinned_todos:
                    p['completed'] = p['id'] in completed_ids
        except Exception:
            pinned_todos = []

    # build payload
    payload = {
        'ok': True,
        'lists_by_category': lists_by_category,
        'categories': categories,
        'pinned_todos': pinned_todos,
        'pagination': {
            'has_prev': has_prev,
            'has_next': has_next,
            'prev_cursor_created_at': (prev_cursor_created_at.isoformat() if prev_cursor_created_at else None),
            'prev_cursor_id': prev_cursor_id,
            'next_cursor_created_at': (next_cursor_created_at.isoformat() if next_cursor_created_at else None),
            'next_cursor_id': next_cursor_id,
        }
    }
    return JSONResponse(payload)


@router.get('/lists/{list_id}', response_class=JSONResponse)
async def client_get_list(request: Request, list_id: int):
    """Return detailed list information (todos, completion types, categories, sublists) for a single list."""
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')

    async with async_session() as sess:
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')

        # completion types
        qct = await sess.scalars(select(CompletionType).where(CompletionType.list_id == list_id).order_by(CompletionType.id.asc()))
        ctypes = qct.all()

        # todos and statuses
        try:
            q2 = await sess.scalars(select(Todo).where(Todo.list_id == list_id).order_by(Todo.priority.desc().nullslast(), Todo.created_at.desc()))
        except Exception:
            q2 = await sess.scalars(select(Todo).where(Todo.list_id == list_id).order_by(Todo.created_at.desc()))
        todos = q2.all()
        todo_ids = [t.id for t in todos]
        ctype_ids = [c.id for c in ctypes]
        status_map: dict[tuple[int,int], bool] = {}
        if todo_ids and ctype_ids:
            qtc = select(TodoCompletion.todo_id, TodoCompletion.completion_type_id, TodoCompletion.done).where(TodoCompletion.todo_id.in_(todo_ids)).where(TodoCompletion.completion_type_id.in_(ctype_ids))
            r = await sess.exec(qtc)
            for tid, cid, done_val in r.all():
                status_map[(tid, cid)] = bool(done_val)

        default_ct = next((c for c in ctypes if c.name == 'default'), None)
        default_id = default_ct.id if default_ct else None

        todo_rows = []
        for t in todos:
            completed_default = False
            if default_id is not None:
                completed_default = status_map.get((t.id, default_id), False)
            extra = []
            for c in ctypes:
                if c.name == 'default':
                    continue
                extra.append({'id': c.id, 'name': c.name, 'done': status_map.get((t.id, c.id), False)})
            todo_rows.append({
                'id': t.id,
                'text': t.text,
                'note': t.note,
                'created_at': (t.created_at.isoformat() if getattr(t, 'created_at', None) else None),
                'modified_at': (t.modified_at.isoformat() if getattr(t, 'modified_at', None) else None),
                'completed': completed_default,
                'pinned': getattr(t, 'pinned', False),
                'priority': getattr(t, 'priority', None),
                'extra_completions': extra,
                'metadata': parse_metadata_json(getattr(t, 'metadata_json', None)),
            })

        # fetch todo tags
        tags_map = {}
        if todo_ids:
            qth = select(TodoHashtag.todo_id, Hashtag.tag).join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id).where(TodoHashtag.todo_id.in_(todo_ids))
            tres = await sess.exec(qth)
            for tid, tag in tres.all():
                tags_map.setdefault(tid, []).append(tag)
        for r in todo_rows:
            r['tags'] = tags_map.get(r['id'], [])

        # list-level hashtags
        ql = select(Hashtag.tag).join(ListHashtag, ListHashtag.hashtag_id == Hashtag.id).where(ListHashtag.list_id == list_id)
        lres = await sess.exec(ql)
        _rows = lres.all()
        list_tags = []
        for row in _rows:
            val = row[0] if isinstance(row, (tuple, list)) else row
            if isinstance(val, str) and val:
                list_tags.append(val)

        list_row = {
            'id': lst.id,
            'name': lst.name,
            'completed': lst.completed,
            'hashtags': list_tags,
            'hide_icons': getattr(lst, 'hide_icons', False),
            'category_id': getattr(lst, 'category_id', None),
            'list_id': lst.id,
            'lists_up_top': getattr(lst, 'lists_up_top', False),
            'priority': getattr(lst, 'priority', None),
            'parent_todo_id': getattr(lst, 'parent_todo_id', None),
            'parent_list_id': getattr(lst, 'parent_list_id', None),
            'metadata': parse_metadata_json(getattr(lst, 'metadata_json', None)),
        }

        if getattr(lst, 'parent_todo_id', None):
            try:
                qpt = await sess.exec(select(Todo.text).where(Todo.id == lst.parent_todo_id))
                row = qpt.first()
                todo_text = row[0] if isinstance(row, (tuple, list)) else row
                if isinstance(todo_text, str):
                    list_row['parent_todo_text'] = todo_text
            except Exception:
                list_row['parent_todo_text'] = None
        if getattr(lst, 'parent_list_id', None):
            try:
                qpl = await sess.exec(select(ListState.name).where(ListState.id == lst.parent_list_id))
                row = qpl.first()
                parent_list_name = row[0] if isinstance(row, (tuple, list)) else row
                if isinstance(parent_list_name, str):
                    list_row['parent_list_name'] = parent_list_name
            except Exception:
                list_row['parent_list_name'] = None

        completion_types = [{'id': c.id, 'name': c.name} for c in ctypes]

        # fetch user's hashtags for suggestions
        owner_id_val = current_user.id
        q_user_list_tags = (
            select(Hashtag.tag)
            .distinct()
            .join(ListHashtag, ListHashtag.hashtag_id == Hashtag.id)
            .join(ListState, ListState.id == ListHashtag.list_id)
            .where(ListState.owner_id == owner_id_val)
        )
        r_user_list_tags = await sess.exec(q_user_list_tags)
        q_user_todo_tags = (
            select(Hashtag.tag)
            .distinct()
            .join(TodoHashtag, TodoHashtag.hashtag_id == Hashtag.id)
            .join(Todo, Todo.id == TodoHashtag.todo_id)
            .join(ListState, ListState.id == Todo.list_id)
            .where(ListState.owner_id == owner_id_val)
        )
        r_user_todo_tags = await sess.exec(q_user_todo_tags)
        _all_rows = list(r_user_list_tags.all()) + list(r_user_todo_tags.all())
        all_hashtags = []
        for row in _all_rows:
            val = row[0] if isinstance(row, (tuple, list)) else row
            if isinstance(val, str) and val not in all_hashtags:
                all_hashtags.append(val)

        # categories (user-scoped)
        try:
            qcat = select(Category).where(Category.owner_id == current_user.id).order_by(Category.position.asc())
            cres = await sess.exec(qcat)
            categories = [{'id': c.id, 'name': c.name, 'position': c.position} for c in cres.all()]
        except Exception:
            categories = []

        # sublists
        sublists = []
        try:
            qsubs = select(ListState).where(ListState.parent_list_id == list_id)
            rsubs = await sess.exec(qsubs)
            rows = rsubs.all()
            def _sort_key(l):
                pos = getattr(l, 'parent_list_position', None)
                created = getattr(l, 'created_at', None)
                return (0 if pos is not None else 1, pos if pos is not None else 0, created or now_utc())
            rows.sort(key=_sort_key)
            sub_ids = [l.id for l in rows if l.id is not None]
            tag_map = {}
            if sub_ids:
                qlh = select(ListHashtag.list_id, Hashtag.tag).join(Hashtag, Hashtag.id == ListHashtag.hashtag_id).where(ListHashtag.list_id.in_(sub_ids))
                rlh = await sess.exec(qlh)
                for lid, tag in rlh.all():
                    tag_map.setdefault(lid, []).append(tag)
            for l in rows:
                sublists.append({
                    'id': l.id,
                    'name': l.name,
                    'completed': getattr(l, 'completed', False),
                    'created_at': getattr(l, 'created_at', None),
                    'modified_at': getattr(l, 'modified_at', None),
                    'hashtags': tag_map.get(l.id, []),
                    'parent_list_position': getattr(l, 'parent_list_position', None),
                    'override_priority': None,
                    'priority': getattr(l, 'priority', None),
                    'metadata': parse_metadata_json(getattr(l, 'metadata_json', None)),
                })
            # compute override priorities per sublist (similar to above)
            try:
                if sub_ids:
                    todo_q = await sess.scalars(select(Todo.id, Todo.list_id, Todo.priority).where(Todo.list_id.in_(sub_ids)).where(Todo.priority != None))
                    todo_id_rows = todo_q.all()
                    todo_map = {}
                    todo_ids = []
                    for tid, lid, pri in todo_id_rows:
                        todo_map.setdefault(lid, []).append((tid, pri))
                        todo_ids.append(tid)
                    completed_ids = set()
                    if todo_ids:
                        try:
                            qcomp = select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(todo_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True)
                            cres = await sess.exec(qcomp)
                            completed_ids = set(r[0] if isinstance(r, tuple) else r for r in cres.all())
                        except Exception:
                            completed_ids = set()
                    for sub in sublists:
                        lid = sub.get('id')
                        candidates = todo_map.get(lid, [])
                        max_p = None
                        for tid, pri in candidates:
                            if tid in completed_ids:
                                continue
                            try:
                                if pri is None:
                                    continue
                                pv = int(pri)
                            except Exception:
                                continue
                            if max_p is None or pv > max_p:
                                max_p = pv
                        if max_p is not None:
                            sub['override_priority'] = max_p
            except Exception:
                pass
        except Exception:
            sublists = []

    payload = {
        'ok': True,
        'list': list_row,
        'todos': todo_rows,
        'completion_types': completion_types,
        'all_hashtags': all_hashtags,
        'categories': categories,
        'sublists': sublists,
    }
    return JSONResponse(payload)


# Todo CRUD endpoints
@router.get('/lists/{list_id}/todos', response_class=JSONResponse)
async def get_list_todos(list_id: int, request: Request):
    """Get all todos for a list that the user owns."""
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')

    async with async_session() as sess:
        # Verify list exists and user owns it
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        
        # Check ownership - handle case where owner_id might not be loaded
        owner_id = getattr(lst, 'owner_id', None)
        if owner_id is not None and owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")

        # Fetch todos for the list
        try:
            q2 = await sess.scalars(select(Todo).where(Todo.list_id == list_id).order_by(Todo.priority.desc().nullslast(), Todo.created_at.desc()))
        except Exception:
            q2 = await sess.scalars(select(Todo).where(Todo.list_id == list_id).order_by(Todo.created_at.desc()))
        todos = q2.all()

        # Serialize todos
        result = []
        for todo in todos:
            result.append({
                "id": todo.id,
                "text": todo.text,
                "pinned": getattr(todo, 'pinned', False),
                "note": todo.note,
                "created_at": todo.created_at.isoformat() if todo.created_at else None,
                "modified_at": todo.modified_at.isoformat() if todo.modified_at else None,
                "deferred_until": todo.deferred_until.isoformat() if todo.deferred_until else None,
                "list_id": todo.list_id,
                "completions": [],
                "priority": getattr(todo, 'priority', None),
                "completed": False  # Will be set by completion logic below
            })

        # Add completion status for all completion types
        todo_ids = [t.id for t in todos]
        if todo_ids:
            # Get all completion types for this list
            qct = await sess.scalars(select(CompletionType).where(CompletionType.list_id == list_id).order_by(CompletionType.id.asc()))
            completion_types = qct.all()
            
            # Get completion status for all completion types
            completion_type_ids = [ct.id for ct in completion_types]
            if completion_type_ids:
                qcomp = await sess.scalars(select(TodoCompletion).where(TodoCompletion.todo_id.in_(todo_ids)).where(TodoCompletion.completion_type_id.in_(completion_type_ids)))
                completion_records = qcomp.all()
                
                # Create completion map: todo_id -> completion_type_id -> done
                completion_map = {}
                for comp in completion_records:
                    if comp.todo_id not in completion_map:
                        completion_map[comp.todo_id] = {}
                    completion_map[comp.todo_id][comp.completion_type_id] = comp.done
                
                # Update todos with completion data
                for todo_data in result:
                    todo_id = todo_data["id"]
                    todo_completions = {}
                    
                    # Set completion status for each completion type
                    for ct in completion_types:
                        todo_completions[ct.name] = completion_map.get(todo_id, {}).get(ct.id, False)
                    
                    todo_data["completions"] = todo_completions
                    
                    # For backward compatibility, set "completed" to the default completion type status
                    default_completion = todo_completions.get("default", False)
                    todo_data["completed"] = default_completion

        return JSONResponse(result)


@router.post('/todos', response_class=JSONResponse)
async def create_todo(request: Request):
    """Create a todo. Expects JSON payload with text, list_id, note, priority."""
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    text = payload.get('text')
    note = payload.get('note')
    list_id = payload.get('list_id')
    priority = payload.get('priority')
    metadata = payload.get('metadata') if isinstance(payload, dict) else None

    if not text or not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text is required and must be a string")
    if list_id is None:
        raise HTTPException(status_code=400, detail="list_id is required")

    try:
        list_id = int(list_id)
    except Exception:
        raise HTTPException(status_code=400, detail="list_id must be an integer")

    if priority is not None:
        try:
            priority = int(priority)
        except Exception:
            raise HTTPException(status_code=400, detail="priority must be an integer")

    # Import the internal function from main
    from .main import _create_todo_internal
    return await _create_todo_internal(text, note, list_id, priority, current_user, metadata=metadata)


@router.patch('/todos/{todo_id}', response_class=JSONResponse)
async def update_todo(todo_id: int, request: Request):
    """Update a todo. Expects JSON payload with optional fields."""
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')

    # Import the internal function from main
    from .main import _update_todo_internal
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    return await _update_todo_internal(todo_id, payload, current_user)


@router.delete('/todos/{todo_id}', response_class=JSONResponse)
async def delete_todo(todo_id: int, request: Request):
    """Delete a todo."""
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')

    # Import the internal function from main
    from .main import _delete_todo_internal
    return await _delete_todo_internal(todo_id, current_user)


@router.get('/lists/{list_id}/completion_types', response_class=JSONResponse)
async def get_list_completion_types(list_id: int, request: Request):
    """Get all completion types for a list that the user owns."""
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')

    async with async_session() as sess:
        # Verify list exists and user owns it
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")

        # Fetch completion types for the list
        qct = await sess.scalars(select(CompletionType).where(CompletionType.list_id == list_id).order_by(CompletionType.id.asc()))
        completion_types = qct.all()

        # Serialize completion types
        result = []
        for ct in completion_types:
            result.append({
                "id": ct.id,
                "name": ct.name,
                "list_id": ct.list_id
            })

        return JSONResponse(result)
