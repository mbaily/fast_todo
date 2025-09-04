"""User-scoped Python REPL helpers for lists/todos.

This module exposes a small, Pythonic API that wraps DB access and enforces
per-user scoping. It is designed to be used from a restricted exec context.

Safety notes:
- Only expose safe helpers and limited builtins to the REPL.
- No imports allowed from user code; no filesystem or subprocess access.
- All DB ops are filtered to the current user.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List as TList, Optional, Tuple, Union, Callable
import asyncio
import threading
import io
import contextlib
import builtins as _bi
import re

from sqlmodel import select

from .db import async_session
from .models import ListState, Todo, Hashtag, ListHashtag, TodoHashtag, User


# --- Thread-local loop management for blocking helpers ---
_tls = threading.local()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    loop = getattr(_tls, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _tls.loop = loop
    return loop


def _run(coro):
    loop = _ensure_loop()
    return loop.run_until_complete(coro)


# --- Small value/printing helpers ---
def _tabulate(rows: list[dict[str, Any]], columns: Optional[list[str]] = None) -> str:
    if not rows:
        return "(no results)"
    if columns is None:
        # union of keys, stable order by first row then new keys encountered
        columns = []
        seen = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    columns.append(k)
    widths = {c: max(len(str(c)), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    def fmt_row(r: dict[str, Any]) -> str:
        return " | ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns)
    header = " | ".join(c.ljust(widths[c]) for c in columns)
    sep = "-+-".join("-" * widths[c] for c in columns)
    body = "\n".join(fmt_row(r) for r in rows)
    return f"{header}\n{sep}\n{body}"


# --- Refs ---
@dataclass
class ListRef:
    id: int
    name: str
    path: str

    def __repr__(self) -> str:
        return f"<List #{self.id} {self.path}>"


@dataclass
class TodoRef:
    id: int
    text: str
    list_path: str

    def __repr__(self) -> str:
        return f"<Todo #{self.id} {self.list_path} :: {self.text}>"


# --- Core context ---
class Repl:
    def __init__(self, user: User):
        self.user = user
        self.cwd: Optional[Union[ListRef, TodoRef]] = None  # container context
        self.output_format: str = "table"

    # -------- Resolution --------
    def L(self, ident: Union[int, str]) -> ListRef:
        """L(id_or_path) -> ListRef

        Resolve a List by id or absolute path like '/Inbox/Work'.
        Relative names are resolved under the current directory (cd).
        """
        if isinstance(ident, int):
            row = _run(self._get_list_by_id(ident))
            if not row:
                raise ValueError("list not found")
            return self._listref(row)
        # path
        path = ident.strip()
        if not path.startswith('/'):
            # relative: interpret under cwd if cwd is a list
            base_path = self.pwd()
            if base_path != '/':
                path = base_path.rstrip('/') + '/' + path
            else:
                path = '/' + path
        row = _run(self._get_list_by_path(path))
        if not row:
            raise ValueError(f"list not found for path: {path}")
        return self._listref(row)

    def T(self, ident: Union[int, Tuple[str, str]]) -> TodoRef:
        """T(id) or T((list_path, text)) -> TodoRef

        Resolve a Todo by integer id or by specifying its list path and exact text.
        """
        if isinstance(ident, int):
            row = _run(self._get_todo_by_id(ident))
            if not row:
                raise ValueError("todo not found")
            return self._todoref(row)
        if isinstance(ident, tuple) and len(ident) == 2:
            list_path, text = ident
            lst = self.L(list_path)
            row = _run(self._get_todo_by_text(lst.id, text))
            if not row:
                raise ValueError("todo not found in list")
            return self._todoref(row)
        raise ValueError("invalid todo selector: use id or (list_path, text)")

    # -------- Navigation --------
    def pwd(self) -> str:
        """pwd() -> str

        Return the current container path (list path) or '/' if at root.
        """
        if isinstance(self.cwd, ListRef):
            return self.cwd.path
        # root if unset or focused on a todo
        return '/'

    def cd(self, target: Optional[Union[str, ListRef]] = None):
        """cd(target=None)

        Change current directory to a List. Use a ListRef or a path string.
        Without arguments, resets to '/'.
        """
        if target is None:
            self.cwd = None
            return self
        if isinstance(target, ListRef):
            self.cwd = target
            return self
        self.cwd = self.L(target)
        return self

    # -------- Listing / showing --------
    def ls(self, selector: Optional[Union[str, ListRef]] = None, recursive: bool = False) -> str:
        """ls(selector=None, recursive=False)

        List lists and todos under the current directory or a given List.
        selector: path string or ListRef
        recursive: when True, includes single-level descendants recursively.
        Returns a table string (default fmt) or JSON list when fmt('json').
        """
        base: Optional[ListRef] = None
        if selector is None:
            if isinstance(self.cwd, ListRef):
                base = self.cwd
        elif isinstance(selector, ListRef):
            base = selector
        else:
            base = self.L(selector)
        rows: list[dict[str, Any]] = []
        if base is None:
            # top-level lists only
            lists = _run(self._lists_in_container(None, None))
            rows = [
                {"type": "list", "id": l.id, "name": l.name, "path": self._listref(l).path}
                for l in lists
            ]
        else:
            lists, todos = _run(self._lists_and_todos_under_list(base.id))
            rows.extend({"type": "list", "id": l.id, "name": l.name, "path": self._listref(l).path} for l in lists)
            rows.extend({"type": "todo", "id": t.id, "text": t.text, "list": base.path} for t in todos)
            if recursive:
                for l in lists:
                    subref = self._listref(l)
                    sub_lists, sub_todos = _run(self._lists_and_todos_under_list(l.id))
                    rows.extend({"type": "list", "id": sl.id, "name": sl.name, "path": self._listref(sl).path} for sl in sub_lists)
                    rows.extend({"type": "todo", "id": st.id, "text": st.text, "list": subref.path} for st in sub_todos)
        return _tabulate(rows, ["type", "id", "name", "text", "path", "list"]) if self.output_format == "table" else rows

    def show(self, x: Union[ListRef, TodoRef, int, str, Tuple[str, str]]):
        """show(item)

        Print properties of a List or Todo. Accepts refs, ids, or (list_path, text).
        Respects output fmt('table'|'json').
        """
        if isinstance(x, ListRef) or (not isinstance(x, tuple) and isinstance(x, (int, str))):
            l = self.L(x) if not isinstance(x, ListRef) else x
            row = _run(self._get_list_by_id(l.id))
            if not row:
                raise ValueError("list not found")
            data = {
                "type": "list", "id": row.id, "name": row.name, "priority": row.priority,
                "path": self._listref(row).path, "completed": row.completed,
            }
            return data if self.output_format != "table" else _tabulate([data])
        # else assume todo
        t = self.T(x) if not isinstance(x, TodoRef) else x
        row = _run(self._get_todo_by_id(t.id))
        if not row:
            raise ValueError("todo not found")
        data = {
            "type": "todo", "id": row.id, "text": row.text, "note": row.note,
            "priority": row.priority, "pinned": row.pinned,
            "list": self._listref(row.list).path if getattr(row, 'list', None) else t.list_path,
        }
        return data if self.output_format != "table" else _tabulate([data])

    # -------- Create --------
    def new_list(self, name: str, at: Optional[Union[ListRef, TodoRef]] = None, **props) -> ListRef:
        """new_list(name, at=None, **props) -> ListRef

        Create a list at root, under a List, or under a Todo (sublist).
        Props: priority, expanded, hide_done, lists_up_top, hide_icons, completed, category_id.
        """
        return _run(self._new_list(name, at, props))

    def new_todo(self, text: str, at: Optional[Union[ListRef, TodoRef]] = None, **props) -> TodoRef:
        """new_todo(text, at=None, **props) -> TodoRef

        Create a todo under a given List (or the todo's parent list when at is a Todo).
        Props: note, pinned, priority, deferred_until, recurrence_*.
        """
        return _run(self._new_todo(text, at, props))

    def add_sublist(self, name: str, to: Union[ListRef, TodoRef], **props) -> ListRef:
        """add_sublist(name, to, **props) -> ListRef

        Convenience wrapper for new_list at a List or Todo container.
        """
        return self.new_list(name, at=to, **props)

    # -------- Edit / move --------
    def rename(self, x: Union[ListRef, TodoRef, int, str, Tuple[str, str]], new_name: str):
        """rename(item, new_name)

        Rename a List (name) or Todo (text).
        """
        if isinstance(x, TodoRef) or isinstance(x, tuple):
            t = self.T(x) if not isinstance(x, TodoRef) else x
            return _run(self._set_todo_fields(t.id, {"text": new_name}))
        l = self.L(x) if not isinstance(x, ListRef) else x
        return _run(self._set_list_fields(l.id, {"name": new_name}))

    def setprop(self, x: Union[ListRef, TodoRef, int, str, Tuple[str, str]], **props):
        """setprop(item, **props)

        Set fields on a List or Todo. Unknown fields are ignored.
        """
        if isinstance(x, TodoRef) or isinstance(x, tuple):
            t = self.T(x) if not isinstance(x, TodoRef) else x
            return _run(self._set_todo_fields(t.id, props))
        l = self.L(x) if not isinstance(x, ListRef) else x
        return _run(self._set_list_fields(l.id, props))

    def mv(self, src: Union[ListRef, TodoRef, int, str, Tuple[str, str]], dest: Union[ListRef, TodoRef, str]):
        """mv(src, dest)

        Move:
        - Todo -> List
        - List -> List
        - List -> Todo (as sublist)
        Cycle-safe; raises on invalid moves.
        """
        # list -> list | todo; todo -> list
        if isinstance(src, TodoRef) or isinstance(src, tuple) or isinstance(src, int):
            t = self.T(src) if not isinstance(src, TodoRef) else src
            dest_list = dest if isinstance(dest, ListRef) else self.L(dest)
            return _run(self._move_todo_to_list(t.id, dest_list.id))
        # list move
        l = self.L(src) if not isinstance(src, ListRef) else src
        if isinstance(dest, TodoRef) or (isinstance(dest, tuple)):
            to_todo = dest if isinstance(dest, TodoRef) else self.T(dest)
            return _run(self._move_list_to_parent(l.id, parent_list_id=None, parent_todo_id=to_todo.id))
        dest_list = dest if isinstance(dest, ListRef) else self.L(dest)
        return _run(self._move_list_to_parent(l.id, parent_list_id=dest_list.id, parent_todo_id=None))

    # -------- Output control --------
    def fmt(self, fmt: str):
        self.output_format = fmt if fmt in ("table", "json") else "table"
        return self

    # ===== Async impls =====
    async def _get_list_by_id(self, list_id: int) -> Optional[ListState]:
        async with async_session() as sess:
            res = await sess.scalars(select(ListState).where(ListState.id == list_id, ListState.owner_id == self.user.id))
            return res.first()

    async def _get_list_by_path(self, path: str) -> Optional[ListState]:
        # path like /A/B/C across parent_list chains
        parts = [p for p in path.strip('/').split('/') if p]
        parent_list_id = None
        parent_todo_id = None
        current: Optional[ListState] = None
        async with async_session() as sess:
            for i, name in enumerate(parts):
                q = select(ListState).where(
                    ListState.name == name,
                    ListState.owner_id == self.user.id,
                    (ListState.parent_list_id == parent_list_id) if parent_list_id is not None else (ListState.parent_list_id == None),
                    (ListState.parent_todo_id == parent_todo_id) if parent_todo_id is not None else (ListState.parent_todo_id == None),
                )
                res = await sess.exec(q)
                current = res.first()
                if not current:
                    return None
                # next segment searches under this list (by default)
                parent_list_id = current.id
                parent_todo_id = None
        return current

    async def _get_todo_by_id(self, todo_id: int) -> Optional[Todo]:
        async with async_session() as sess:
            res = await sess.scalars(select(Todo).where(Todo.id == todo_id))
            row = res.first()
            if not row:
                return None
            # ensure ownership via parent list
            lres = await sess.scalars(select(ListState).where(ListState.id == row.list_id, ListState.owner_id == self.user.id))
            if not lres.first():
                return None
            return row

    async def _get_todo_by_text(self, list_id: int, text: str) -> Optional[Todo]:
        async with async_session() as sess:
            res = await sess.scalars(select(Todo).where(Todo.list_id == list_id, Todo.text == text))
            return res.first()

    async def _lists_in_container(self, parent_list_id: Optional[int], parent_todo_id: Optional[int]) -> list[ListState]:
        async with async_session() as sess:
            q = select(ListState).where(ListState.owner_id == self.user.id)
            if parent_list_id is None:
                q = q.where(ListState.parent_list_id == None)
            else:
                q = q.where(ListState.parent_list_id == parent_list_id)
            if parent_todo_id is None:
                q = q.where(ListState.parent_todo_id == None)
            else:
                q = q.where(ListState.parent_todo_id == parent_todo_id)
            res = await sess.exec(q.order_by(ListState.created_at.asc()))
            return res.all()

    async def _lists_and_todos_under_list(self, list_id: int) -> tuple[list[ListState], list[Todo]]:
        async with async_session() as sess:
            lq = await sess.scalars(select(ListState).where(ListState.owner_id == self.user.id, ListState.parent_list_id == list_id))
            tq = await sess.exec(select(Todo).where(Todo.list_id == list_id))
            return lq.all(), tq.all()

    async def _new_list(self, name: str, at: Optional[Union[ListRef, TodoRef]], props: dict) -> ListRef:
        async with async_session() as sess:
            lst = ListState(name=name, owner_id=self.user.id)
            if at is None:
                pass
            elif isinstance(at, ListRef):
                lst.parent_list_id = at.id
            elif isinstance(at, TodoRef):
                lst.parent_todo_id = at.id
            # copy known props
            for k in ["priority", "expanded", "hide_done", "lists_up_top", "hide_icons", "completed", "category_id"]:
                if k in props:
                    setattr(lst, k, props[k])
            sess.add(lst)
            await sess.commit()
            await sess.refresh(lst)
            return self._listref(lst)

    async def _new_todo(self, text: str, at: Optional[Union[ListRef, TodoRef]], props: dict) -> TodoRef:
        async with async_session() as sess:
            dest_list_id: Optional[int] = None
            if at is None:
                # default to a top-level list named 'default' or the newest list
                q = await sess.scalars(select(ListState).where(ListState.owner_id == self.user.id, ListState.parent_list_id == None, ListState.parent_todo_id == None).order_by(ListState.created_at.desc()))
                lst = q.first()
                if not lst:
                    # create a personal root list if none
                    lst = ListState(name="My List", owner_id=self.user.id)
                    sess.add(lst)
                    await sess.commit()
                    await sess.refresh(lst)
                dest_list_id = lst.id
            elif isinstance(at, ListRef):
                dest_list_id = at.id
            elif isinstance(at, TodoRef):
                # cannot add todo under todo; put it under the todo's parent list
                trow = await sess.get(Todo, at.id)
                dest_list_id = trow.list_id
            todo = Todo(text=text, list_id=dest_list_id)
            for k in ["note", "pinned", "priority", "deferred_until", "lists_up_top", "recurrence_rrule", "recurrence_meta", "recurrence_dtstart", "recurrence_parser_version"]:
                if k in props:
                    setattr(todo, k, props[k])
            sess.add(todo)
            await sess.commit()
            await sess.refresh(todo)
            return self._todoref(todo)

    async def _set_list_fields(self, list_id: int, props: dict) -> dict:
        async with async_session() as sess:
            row = await sess.get(ListState, list_id)
            if not row or row.owner_id != self.user.id:
                raise ValueError("list not found")
            for k, v in props.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            sess.add(row)
            await sess.commit()
            await sess.refresh(row)
            return {"id": row.id, "name": row.name}

    async def _set_todo_fields(self, todo_id: int, props: dict) -> dict:
        async with async_session() as sess:
            row = await sess.get(Todo, todo_id)
            if not row:
                raise ValueError("todo not found")
            # check ownership
            l = await sess.get(ListState, row.list_id)
            if not l or l.owner_id != self.user.id:
                raise ValueError("forbidden")
            for k, v in props.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            sess.add(row)
            await sess.commit()
            await sess.refresh(row)
            return {"id": row.id, "text": row.text}

    async def _move_todo_to_list(self, todo_id: int, dest_list_id: int) -> dict:
        async with async_session() as sess:
            t = await sess.get(Todo, todo_id)
            if not t:
                raise ValueError("todo not found")
            src_list = await sess.get(ListState, t.list_id)
            dest_list = await sess.get(ListState, dest_list_id)
            if not src_list or not dest_list or src_list.owner_id != self.user.id or dest_list.owner_id != self.user.id:
                raise ValueError("forbidden or not found")
            t.list_id = dest_list_id
            sess.add(t)
            await sess.commit()
            await sess.refresh(t)
            return {"id": t.id, "list_id": t.list_id}

    async def _move_list_to_parent(self, list_id: int, parent_list_id: Optional[int], parent_todo_id: Optional[int]) -> dict:
        if parent_list_id and parent_todo_id:
            raise ValueError("list can have only one parent: list or todo")
        async with async_session() as sess:
            lst = await sess.get(ListState, list_id)
            if not lst or lst.owner_id != self.user.id:
                raise ValueError("list not found")
            # cycle prevention for list->list moves
            if parent_list_id:
                if list_id == parent_list_id:
                    raise ValueError("cannot move a list into itself")
                # is dest in subtree of src? Walk up from dest
                cur = await sess.get(ListState, parent_list_id)
                while cur is not None:
                    if cur.id == list_id:
                        raise ValueError("cannot move into own subtree")
                    if cur.parent_list_id:
                        cur = await sess.get(ListState, cur.parent_list_id)
                    elif cur.parent_todo_id:
                        # jump to todo's parent list
                        t = await sess.get(Todo, cur.parent_todo_id)
                        cur = await sess.get(ListState, t.list_id) if t else None
                    else:
                        cur = None
            lst.parent_list_id = parent_list_id
            lst.parent_todo_id = parent_todo_id
            sess.add(lst)
            await sess.commit()
            await sess.refresh(lst)
            return {"id": lst.id, "parent_list_id": lst.parent_list_id, "parent_todo_id": lst.parent_todo_id}

    # --- ref builders ---
    def _listref(self, row: ListState) -> ListRef:
        # compute path by walking up parent_list chain
        names = [row.name]
        parent_list_id = row.parent_list_id
        parent_todo_id = row.parent_todo_id
        # For now, only embed parent_list names in path; if parent is a todo,
        # show as '/.../<list>(@todo:<id>)' to disambiguate.
        loop_guard = 0
        while parent_list_id and loop_guard < 100:
            loop_guard += 1
            parent = _run(self._get_list_by_id(parent_list_id))
            if not parent:
                break
            names.append(parent.name)
            parent_list_id = parent.parent_list_id
            parent_todo_id = parent.parent_todo_id
        path = "/" + "/".join(reversed(names))
        if parent_todo_id:
            path = f"{path}(@todo:{parent_todo_id})"
        return ListRef(id=row.id, name=row.name, path=path)

    def _todoref(self, row: Todo) -> TodoRef:
        # load parent list path
        l = _run(self._get_list_by_id(row.list_id))
        lref = self._listref(l) if l else ListRef(row.list_id, f"list:{row.list_id}", f"/list:{row.list_id}")
        return TodoRef(id=row.id, text=row.text, list_path=lref.path)


# --- Exec sandbox ---
FORBIDDEN_NAMES = {"__import__", "open", "exec", "eval", "compile", "globals", "locals", "vars", "input", "help", "breakpoint", "quit", "exit"}


SAFE_BUILTINS = {
    "len": _bi.len,
    "min": _bi.min,
    "max": _bi.max,
    "sum": _bi.sum,
    "sorted": _bi.sorted,
    "any": _bi.any,
    "all": _bi.all,
    "enumerate": _bi.enumerate,
    "range": _bi.range,
    "print": _bi.print,
}


def _reject_unsafe(code: str):
    # very conservative checks
    if re.search(r"\bimport\b|\bos\b|\bsys\b|\bsubprocess\b", code):
        raise ValueError("forbidden token in code")
    if "__" in code:
        raise ValueError("double underscore not allowed")


def run_code_for_user(user: User, code: str, repl: Optional[Repl] = None, env_locals: Optional[dict] = None) -> tuple[str, Any]:
    """Execute code in a restricted environment and return (stdout, last_value).

    Note: This function is blocking; run it in a thread from async routes.
    """
    _reject_unsafe(code)
    # Allow callers (SSH session) to provide a persistent Repl instance so
    # navigation state (cwd, output_format) persists across commands.
    if repl is None:
        repl = Repl(user)
    # dynamic help function printing available commands/docstrings
    def _help(obj: Any = None):
        cmds = {
            'L': 'Resolve List by id or path',
            'T': 'Resolve Todo by id or (list_path, text)',
            'ls': 'List lists/todos under cwd or given list',
            'show': 'Show properties of a list or todo',
            'cd': 'Change current directory to a list',
            'pwd': 'Print current directory path',
            'new_list': 'Create a new list (optionally under list/todo)',
            'new_todo': 'Create a new todo under a list',
            'add_sublist': 'Create sublist under list/todo',
            'rename': 'Rename a list or todo',
            'setprop': 'Set fields on a list or todo',
            'mv': 'Move list/todo to new container',
            'fmt': "Set output format: 'table' or 'json'",
        }
        if obj is None:
            print("Commands:")
            for k in sorted(cmds):
                print(f"  {k:<10} - {cmds[k]}")
            print("\nDetails: help(func) e.g., help(ls)")
            return
        doc = getattr(obj, '__doc__', None)
        if not doc:
            print("No documentation available.")
            return
        print(doc.strip())

    env_globals = {
        "__builtins__": SAFE_BUILTINS,
        # helpers
        "L": repl.L,
        "T": repl.T,
        "ls": repl.ls,
        "tree": None,  # not implemented yet
        "show": repl.show,
        "cd": repl.cd,
        "pwd": repl.pwd,
        "new_list": repl.new_list,
        "new_todo": repl.new_todo,
        "add_sublist": repl.add_sublist,
        "rename": repl.rename,
        "setprop": repl.setprop,
        "mv": repl.mv,
        "fmt": repl.fmt,
        "help": _help,
        # context
        "me": user,
        "cwd": repl.cwd,
    }
    # Allow callers to pass a persistent locals dict so assignments persist
    # across multiple invocations (useful for SSH session state).
    if env_locals is None:
        env_locals = {}
    buf = io.StringIO()
    last_val: Any = None
    try:
        with contextlib.redirect_stdout(buf):
            # Try to eval as expression first; if fails, exec as statements
            try:
                compiled = compile(code, "<repl>", "eval")
                last_val = eval(compiled, env_globals, env_locals)
            except SyntaxError:
                compiled = compile(code, "<repl>", "exec")
                exec(compiled, env_globals, env_locals)
    finally:
        out = buf.getvalue()
    return out, last_val
