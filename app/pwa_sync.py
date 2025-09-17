from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from .auth import require_login
from .db import async_session
from .models import ListState, Todo, Tombstone, SyncOperation, PushSubscription, User, Category
from .utils import now_utc
from sqlalchemy import select
import json
import logging

router = APIRouter()


class SyncOp(BaseModel):
    op: str
    payload: dict


class SyncRequest(BaseModel):
    ops: List[SyncOp]


@router.get('/sync')
async def sync_get(since: Optional[str] = None, current_user: User = Depends(require_login)):
    since_dt = None
    if since:
        try:
            from datetime import datetime
            since_dt = datetime.fromisoformat(since)
        except Exception:
            raise HTTPException(status_code=400, detail='invalid since timestamp')
    async with async_session() as sess:
        ql = select(ListState).where((ListState.owner_id == current_user.id) | (ListState.owner_id is None))
        if since_dt:
            ql = ql.where(ListState.modified_at is not None).where(ListState.modified_at > since_dt)
        resl = await sess.exec(ql)
        list_objs = resl.scalars().all()
        lists = [
            {
                "id": lst.id,
                "name": lst.name,
                "owner_id": lst.owner_id,
                "created_at": (lst.created_at.isoformat() if lst.created_at else None),
                "modified_at": (lst.modified_at.isoformat() if lst.modified_at else None),
                "category_id": lst.category_id,
                "parent_todo_id": lst.parent_todo_id,
                "parent_list_id": lst.parent_list_id,
            }
            for lst in list_objs
        ]

        # categories (user-scoped)
        qc = select(Category).where(Category.owner_id == current_user.id)
        resc = await sess.exec(qc)
        category_objs = resc.scalars().all()
        categories = [
            {
                "id": cat.id,
                "name": cat.name,
                "position": cat.position,
                "sort_alphanumeric": cat.sort_alphanumeric,
                "owner_id": cat.owner_id,
            }
            for cat in category_objs
        ]

        # todos in those lists
        list_ids = [item['id'] for item in lists]
        qt = select(Todo)
        if list_ids:
            qt = qt.where(Todo.list_id.in_(list_ids))
        else:
            if since_dt:
                qt = qt.where(Todo.modified_at != None)  # noqa: E711
                qt = qt.where(Todo.modified_at > since_dt)
            else:
                qt = qt.where(False)
        rest = await sess.exec(qt)
        todo_objs = rest.scalars().all()

    todos = []
    for t in todo_objs:
        todos.append(
            {
                "id": t.id,
                "text": t.text,
                "note": t.note,
                "created_at": (t.created_at.isoformat() if t.created_at else None),
                "modified_at": (t.modified_at.isoformat() if t.modified_at else None),
                "list_id": t.list_id,
            }
        )

    # tombstones
    tombstones = []
    if since_dt:
        qtomb = select(Tombstone).where(Tombstone.created_at != None)  # noqa: E711
        qtomb = qtomb.where(Tombstone.created_at > since_dt)
        tres = await sess.exec(qtomb)
        tomb_objs = tres.scalars().all()
        tombstones = [
            {"item_type": t.item_type, "item_id": t.item_id, "created_at": (t.created_at.isoformat() if t.created_at else None)}
            for t in tomb_objs
        ]

    return {"lists": lists, "todos": todos, "categories": categories, "tombstones": tombstones, "server_ts": now_utc().isoformat()}


@router.post('/sync')
async def sync_post(req: SyncRequest, current_user: User = Depends(require_login)):
    results: List[Dict[str, Any]] = []
    async with async_session() as sess:
        for op in req.ops:
            name = op.op
            payload = op.payload or {}
            op_id = payload.get('op_id')

            # Check idempotency
            if op_id:
                qop = await sess.exec(select(SyncOperation).where(SyncOperation.op_id == op_id).where(SyncOperation.user_id == current_user.id))
                existing = qop.scalars().first()
                if existing:
                    try:
                        prev = json.loads(existing.result_json) if existing.result_json else {'op': name, 'status': 'ok', 'id': existing.server_id}
                    except Exception:
                        prev = {'op': name, 'status': 'ok', 'id': existing.server_id}
                    results.append(prev)
                    continue

            try:
                # Minimal set of ops supported: create_list, delete_list, create_todo, update_todo, delete_todo
                if name == 'create_list':
                    client_id = payload.get('client_id')
                    lst = ListState(name=payload.get('name'), owner_id=current_user.id)
                    sess.add(lst)
                    await sess.commit()
                    await sess.refresh(lst)
                    out = {'op': name, 'status': 'ok', 'id': lst.id}
                    if client_id is not None:
                        out['client_id'] = client_id
                    results.append(out)
                    if op_id:
                        so = SyncOperation(user_id=current_user.id, op_id=op_id, op_name=name, client_id=client_id, server_id=lst.id, result_json=json.dumps(out))
                        sess.add(so)
                        await sess.commit()

                elif name == 'create_todo':
                    # payload: text, note, list_id, priority, client_id, op_id
                    client_id = payload.get('client_id')
                    text = payload.get('text')
                    note = payload.get('note')
                    list_id = payload.get('list_id')
                    priority = payload.get('priority')
                    # validate minimal fields
                    if not text or list_id is None:
                        results.append({'op': name, 'status': 'error', 'reason': 'text and list_id required'})
                    else:
                        try:
                            from .main import _create_todo_internal

                            todo_resp = await _create_todo_internal(text=text, note=note, list_id=int(list_id), priority=priority, current_user=current_user)
                            out = {'op': name, 'status': 'ok', 'id': todo_resp.get('id')}
                            if client_id is not None:
                                out['client_id'] = client_id
                            results.append(out)
                            if op_id:
                                so = SyncOperation(user_id=current_user.id, op_id=op_id, op_name=name, client_id=client_id, server_id=out.get('id'), result_json=json.dumps(out))
                                sess.add(so)
                                await sess.commit()
                        except Exception:
                            results.append({'op': name, 'status': 'error'})

                elif name == 'update_todo':
                    # payload: id (or todo_id), fields to update, op_id
                    todo_id = payload.get('id') or payload.get('todo_id')
                    if not todo_id:
                        results.append({'op': name, 'status': 'error', 'reason': 'todo_id required'})
                    else:
                        try:
                            # conflict detection: client may send base_modified_at (ISO string)
                            base = payload.get('base_modified_at')
                            # fetch current todo to compare timestamps
                            cur = await sess.get(Todo, int(todo_id))
                            if cur is None:
                                results.append({'op': name, 'status': 'error', 'reason': 'not_found'})
                                continue
                            if base:
                                try:
                                    from datetime import datetime

                                    base_dt = datetime.fromisoformat(base)
                                    # if server modified_at (or created_at if modified_at is missing) is newer than base_dt, return conflict
                                    server_time = getattr(cur, 'modified_at', None) or getattr(cur, 'created_at', None)
                                    # normalize datetimes to timezone-aware UTC for reliable comparison
                                    try:
                                        from datetime import timezone
                                        if server_time is not None and getattr(server_time, 'tzinfo', None) is None:
                                            server_time = server_time.replace(tzinfo=timezone.utc)
                                        if base_dt is not None and getattr(base_dt, 'tzinfo', None) is None:
                                            base_dt = base_dt.replace(tzinfo=timezone.utc)
                                    except Exception:
                                        pass
                                    try:
                                        logging.getLogger('pwa_sync').info(f"conflict debug base={base_dt!r} server_time={server_time!r}")
                                    except Exception:
                                        pass
                                    if server_time and server_time > base_dt:
                                        # serialize server copy
                                        server_copy = {"id": cur.id, "text": cur.text, "note": cur.note, "modified_at": (server_time.isoformat() if server_time else None), "list_id": cur.list_id}
                                        results.append({'op': name, 'status': 'conflict', 'server': server_copy})
                                        continue
                                except Exception:
                                    # if parse fails, ignore conflict detection and proceed
                                    pass

                            from .main import _update_todo_internal

                            todo_resp = await _update_todo_internal(int(todo_id), payload, current_user)
                            out = {'op': name, 'status': 'ok', 'id': todo_resp.get('id')}
                            results.append(out)
                            if op_id:
                                so = SyncOperation(user_id=current_user.id, op_id=op_id, op_name=name, client_id=payload.get('client_id'), server_id=out.get('id'), result_json=json.dumps(out))
                                sess.add(so)
                                await sess.commit()
                        except HTTPException as he:
                            results.append({'op': name, 'status': 'error', 'detail': getattr(he, 'detail', None)})
                        except Exception:
                            results.append({'op': name, 'status': 'error'})

                elif name == 'delete_todo':
                    todo_id = payload.get('id') or payload.get('todo_id')
                    if not todo_id:
                        results.append({'op': name, 'status': 'error', 'reason': 'todo_id required'})
                    else:
                        try:
                            from .main import _delete_todo_internal

                            await _delete_todo_internal(int(todo_id), current_user)
                            out = {'op': name, 'status': 'ok', 'id': int(todo_id)}
                            results.append(out)
                            if op_id:
                                so = SyncOperation(user_id=current_user.id, op_id=op_id, op_name=name, client_id=payload.get('client_id'), server_id=int(todo_id), result_json=json.dumps(out))
                                sess.add(so)
                                await sess.commit()
                        except HTTPException as he:
                            results.append({'op': name, 'status': 'error', 'detail': getattr(he, 'detail', None)})
                        except Exception:
                            results.append({'op': name, 'status': 'error'})

                else:
                    results.append({'op': name, 'status': 'unsupported'})
            except Exception:
                results.append({'op': name, 'status': 'error'})
    return {'results': results}


# Serve service worker and manifest at root paths so SW can control origin
@router.get('/service-worker.js')
async def service_worker_js():
    return FileResponse('static/service-worker.js', media_type='application/javascript', headers={'Cache-Control': 'no-cache'})


@router.get('/manifest.json')
async def manifest_json():
    return FileResponse('static/manifest.json', media_type='application/manifest+json', headers={'Cache-Control': 'no-cache'})


# Basic push subscription endpoints (store subscription payloads in DB)
class PushSubIn(BaseModel):
    endpoint: str
    keys: Dict[str, str] = {}


@router.post('/push/subscribe')
async def push_subscribe(payload: PushSubIn, current_user: User = Depends(require_login)):
    # store minimal subscription info in PushSubscription.result_json-like field
    async with async_session() as sess:
        ps = PushSubscription(
            user_id=current_user.id,
            subscription_json=json.dumps({'endpoint': payload.endpoint, 'keys': payload.keys}),
        )
        sess.add(ps)
        await sess.commit()
        await sess.refresh(ps)
        return JSONResponse({'ok': True, 'id': ps.id})


@router.post('/push/unsubscribe')
async def push_unsubscribe(id: int, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        q = await sess.get(PushSubscription, id)
        if not q or q.user_id != current_user.id:
            raise HTTPException(status_code=404, detail='not found')
        await sess.delete(q)
        await sess.commit()
    return JSONResponse({'ok': True})


@router.post('/push/send_test')
async def push_send_test(user_id: Optional[int] = None, current_user: User = Depends(require_login)):
    # admin-only helper: send a test push to a user's subscriptions if pywebpush is installed
    if not getattr(current_user, 'is_admin', False):
        raise HTTPException(status_code=403, detail='admin required')
    try:
        from pywebpush import webpush
    except Exception:
        raise HTTPException(status_code=500, detail='pywebpush not installed')
    async with async_session() as sess:
        q = await sess.exec(select(PushSubscription).where(PushSubscription.user_id == (user_id or current_user.id)))
        subs = q.scalars().all()
        sent = 0
        for s in subs:
            try:
                info = json.loads(s.subscription_json)
                # NOTE: VAPID keys must be configured via env or config; placeholder used here
                vapid_private = None
                vapid_claims = {"sub": "mailto:admin@example.com"}
                webpush(subscription_info=info, data='Test', vapid_private_key=vapid_private, vapid_claims=vapid_claims)
                sent += 1
            except Exception:
                continue
    return JSONResponse({'ok': True, 'sent': sent})
