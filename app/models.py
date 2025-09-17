from typing import List, Optional
from datetime import datetime
from .utils import now_utc
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import UniqueConstraint


class TodoHashtag(SQLModel, table=True):
    todo_id: Optional[int] = Field(default=None, foreign_key="todo.id", primary_key=True)
    hashtag_id: Optional[int] = Field(default=None, foreign_key="hashtag.id", primary_key=True)


class ListHashtag(SQLModel, table=True):
    list_id: Optional[int] = Field(default=None, foreign_key="liststate.id", primary_key=True)
    hashtag_id: Optional[int] = Field(default=None, foreign_key="hashtag.id", primary_key=True)


class ServerState(SQLModel, table=True):
    """Singleton-ish table to store server-level settings like the default list id."""
    id: Optional[int] = Field(default=None, primary_key=True)
    default_list_id: Optional[int] = Field(default=None, foreign_key="liststate.id")


class ListState(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # Name uniqueness is enforced per-owner via a DB index (see app/db.py)
    name: str
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: datetime | None = Field(default_factory=now_utc)
    modified_at: datetime | None = Field(default_factory=now_utc)
    expanded: bool = Field(default=True)
    hide_done: bool = Field(default=False)
    # When true, render this list's sublists "up top" near the title
    lists_up_top: bool = Field(default=False)
    # If true, hide UI action icons (completion checkbox, pin, delete) for this list
    hide_icons: bool = Field(default=False)
    completed: bool = Field(default=False)
    # Optional category membership
    category_id: Optional[int] = Field(default=None, foreign_key="category.id", index=True)
    # Optional parent todo owner for recursive lists (sublists). When set, this
    # list is considered a child of the given Todo and should be hidden from
    # the root index views.
    parent_todo_id: Optional[int] = Field(default=None, foreign_key="todo.id", index=True)
    # Position among siblings when this list is a sublist of a Todo. When NULL,
    # ordering will fall back to created_at. Positions are normalized to
    # contiguous 0..N-1 per parent_todo_id when reordering via API/UI.
    parent_todo_position: Optional[int] = Field(default=None, index=True)
    # Optional parent list owner for nested lists (sublists of lists). When set,
    # this list is considered a child of the given ListState and should be
    # rendered on that list's page under a 'Sublists' section.
    parent_list_id: Optional[int] = Field(default=None, foreign_key="liststate.id", index=True)
    # Position among siblings when this list is a sublist of a List. When NULL,
    # ordering will fall back to created_at. Positions are normalized to
    # contiguous 0..N-1 per parent_list_id when reordering via UI.
    parent_list_position: Optional[int] = Field(default=None, index=True)
    # Optional per-list priority: 1 (lowest) .. 10 (highest). Null means no priority.
    priority: Optional[int] = Field(default=None, index=True)

    todos: List["Todo"] = Relationship(
        back_populates="list",
        sa_relationship_kwargs={
            "primaryjoin": "ListState.id==Todo.list_id",
            "foreign_keys": "Todo.list_id",
        },
    )
    hashtags: List["Hashtag"] = Relationship(back_populates="lists", link_model=ListHashtag)
    completion_types: List["CompletionType"] = Relationship(back_populates="list")
    # Relationship to owning parent todo (if any)
    parent_todo: Optional["Todo"] = Relationship(
        back_populates="child_lists",
        sa_relationship_kwargs={
            "primaryjoin": "ListState.parent_todo_id==Todo.id",
            "foreign_keys": "ListState.parent_todo_id",
        },
    )


class Category(SQLModel, table=True):
    """Category grouping for lists. Position is an integer controlling order on index."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    position: int = Field(default=0, index=True)
    # When true, lists under this category should be sorted alphanumerically by name
    sort_alphanumeric: bool = Field(default=False, index=True)



class Hashtag(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tag: str = Field(sa_column_kwargs={"unique": True, "index": True})
    todos: List["Todo"] = Relationship(back_populates="hashtags", link_model=TodoHashtag)
    lists: List[ListState] = Relationship(back_populates="hashtags", link_model=ListHashtag)


class CompletionType(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    list_id: Optional[int] = Field(default=None, foreign_key="liststate.id")
    __table_args__ = (UniqueConstraint('list_id', 'name'),)

    list: Optional[ListState] = Relationship(back_populates="completion_types")
    completions: List["TodoCompletion"] = Relationship(back_populates="completion_type")


class Todo(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    text: str
    note: Optional[str] = None
    pinned: bool = Field(default=False)
    # When true, exclude this todo from search results
    search_ignored: bool = Field(default=False, index=True)
    # When true, exclude this todo from calendar extraction/occurrences
    calendar_ignored: bool = Field(default=False, index=True)
    created_at: datetime | None = Field(default_factory=now_utc)
    modified_at: datetime | None = Field(default_factory=now_utc)
    deferred_until: Optional[datetime] = None
    # Recurrence metadata: persisted parsed recurrence info to avoid reparsing
    recurrence_rrule: Optional[str] = None
    recurrence_meta: Optional[str] = None  # JSON-encoded string
    recurrence_dtstart: Optional[datetime] = None
    recurrence_parser_version: Optional[str] = None
    # Every Todo must belong to a ListState. Make list_id required (non-optional)
    # so creation will fail if no list_id is provided.
    list_id: int = Field(foreign_key="liststate.id")
    # Optional per-todo priority: 1 (lowest) .. 10 (highest). Null means no priority.
    priority: Optional[int] = Field(default=None, index=True)
    # When true, render this todo's child sublists above tags/other metadata
    lists_up_top: bool = Field(default=False)
    # When true, sort inline fn:link occurrences by priority when rendering the note
    sort_links: bool = Field(default=False)

    # Relationship should reflect that a todo always has a parent list.
    list: ListState = Relationship(
        back_populates="todos",
        sa_relationship_kwargs={
            "primaryjoin": "Todo.list_id==ListState.id",
            "foreign_keys": "Todo.list_id",
        },
    )
    hashtags: List[Hashtag] = Relationship(back_populates="todos", link_model=TodoHashtag)
    completions: List["TodoCompletion"] = Relationship(back_populates="todo")
    # Sublists that are owned by this todo (recursive lists feature)
    child_lists: List[ListState] = Relationship(
        back_populates="parent_todo",
        sa_relationship_kwargs={
            "primaryjoin": "Todo.id==ListState.parent_todo_id",
            "foreign_keys": "ListState.parent_todo_id",
        },
    )


class TodoCompletion(SQLModel, table=True):
    todo_id: Optional[int] = Field(default=None, foreign_key="todo.id", primary_key=True)
    completion_type_id: Optional[int] = Field(default=None, foreign_key="completiontype.id", primary_key=True)
    done: bool = Field(default=False)

    todo: Optional[Todo] = Relationship(back_populates="completions")
    completion_type: Optional[CompletionType] = Relationship(back_populates="completions")


class User(SQLModel, table=True):
    """Basic user model for future auth: password stored as bcrypt hash."""
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, sa_column_kwargs={"unique": True})
    password_hash: str
    is_admin: bool = Field(default=False)
    # Optional per-user default category for newly-created lists.
    # When set, new lists created by this user should be assigned this category.
    default_category_id: Optional[int] = Field(default=None, foreign_key="category.id")
    # Optional per-user collation list id: when set, indicates which list acts
    # as the user's personal collation/collection. Todos can be "linked" to
    # this list via ItemLink rows (src_type='list', src_id=collation_list_id,
    # tgt_type='todo', tgt_id=<todo_id>).
    collation_list_id: Optional[int] = Field(default=None, foreign_key="liststate.id", index=True)
    # When true, show a linked/unlinked indicator on todo pages for this user,
    # allowing quick add/remove from their collation list.
    show_collation_indicator: bool = Field(default=False, index=True)


class Session(SQLModel, table=True):
    """Server-side session store for browser clients.

    session_token is a secure random string stored in an HttpOnly cookie and
    mapped to a user_id in the DB. Expires_at is optional; cleanup is best-effort
    and handled by DB maintenance tasks or token rotation.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    session_token: str = Field(sa_column_kwargs={"unique": True, "index": True})
    user_id: int = Field(foreign_key="user.id", index=True)
    created_at: datetime | None = Field(default_factory=now_utc)
    expires_at: Optional[datetime] = None
    timezone: Optional[str] = None


class SyncOperation(SQLModel, table=True):
    """Record of a processed sync operation to support idempotency for PWA clients."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    op_id: str = Field(sa_column_kwargs={"unique": True, "index": True})
    op_name: Optional[str] = None
    client_id: Optional[str] = None
    server_id: Optional[int] = None
    result_json: Optional[str] = None
    created_at: datetime | None = Field(default_factory=now_utc)


class Tombstone(SQLModel, table=True):
    """Simple tombstone table to record deletions for sync clients.

    item_type: 'todo' | 'list' (string)
    item_id: the integer id of the deleted item
    created_at: timestamp when deletion recorded
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    item_type: str
    item_id: int
    created_at: datetime | None = Field(default_factory=now_utc)


class RecentListVisit(SQLModel, table=True):
    """Per-user record of recently visited lists.

    Composite primary key (user_id, list_id) enforces one row per pair; visited_at
    is updated on repeat visits. Indexed for fast per-user lookup ordered by
    visited_at.
    """
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", primary_key=True)
    list_id: Optional[int] = Field(default=None, foreign_key="liststate.id", primary_key=True)
    visited_at: datetime | None = Field(default_factory=now_utc, index=True)
    # position: integer position for top-N ordering (0 = top). NULL/None means not in top-N.
    position: Optional[int] = Field(default=None, index=True)


class RecentTodoVisit(SQLModel, table=True):
    """Per-user record of recently visited todos.

    Composite primary key (user_id, todo_id) with visited_at timestamp and optional
    position for preserving a top-N pin order (0 = top). Mirrors RecentListVisit.
    """
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", primary_key=True)
    todo_id: Optional[int] = Field(default=None, foreign_key="todo.id", primary_key=True)
    visited_at: datetime | None = Field(default_factory=now_utc, index=True)
    position: Optional[int] = Field(default=None, index=True)


class CompletedOccurrence(SQLModel, table=True):
    """Persisted completed occurrence hashes per-user."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    occ_hash: str = Field(index=True, sa_column_kwargs={"unique": False})
    item_type: Optional[str] = None
    item_id: Optional[int] = None
    occurrence_dt: Optional[datetime] = None
    completed_at: datetime | None = Field(default_factory=now_utc)


class TrashMeta(SQLModel, table=True):
    """Metadata for todos moved to the trash so we can restore them.

    Stores the original list_id and a tombstone-like timestamp of when the
    todo was trashed. Kept minimal to avoid coupling with the Todo model.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    todo_id: int = Field(foreign_key='todo.id', index=True)
    original_list_id: Optional[int] = Field(default=None, foreign_key='liststate.id', index=True)
    trashed_at: datetime | None = Field(default_factory=now_utc, index=True)


class ListTrashMeta(SQLModel, table=True):
    """Metadata for lists moved to the trash so we can restore them.

    Stores the original parent_list_id and original owner_id along with
    a trashed_at timestamp. Kept minimal to avoid coupling with ListState.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    list_id: int = Field(foreign_key='liststate.id', index=True)
    original_parent_list_id: Optional[int] = Field(default=None, foreign_key='liststate.id', index=True)
    original_owner_id: Optional[int] = Field(default=None, foreign_key='user.id', index=True)
    trashed_at: datetime | None = Field(default_factory=now_utc, index=True)


class IgnoredScope(SQLModel, table=True):
    """Records ignore rules per-user (list-wide or todo-from-date).

    scope_type: 'list' or 'todo_from'
    scope_key: textual key (list id or todo id)
    from_dt: optional datetime for todo_from
    scope_hash: the canonical hash produced by ignore_list_hash or ignore_todo_from_hash
    active: whether the ignore is currently active
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    scope_type: str
    scope_key: str
    from_dt: Optional[datetime] = None
    scope_hash: str = Field(index=True)
    created_at: datetime | None = Field(default_factory=now_utc)
    active: bool = Field(default=True, index=True)


class SshPublicKey(SQLModel, table=True):
    """SSH public keys associated with an app user for SSH REPL auth."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    public_key: str  # OpenSSH-formatted public key line
    comment: Optional[str] = None
    fingerprint: Optional[str] = Field(default=None, index=True)
    enabled: bool = Field(default=True, index=True)
    created_at: datetime | None = Field(default_factory=now_utc)


class PushSubscription(SQLModel, table=True):
    """Stores a user's Web Push subscription details as JSON.

    subscription_json: JSON-encoded dict with endpoint and keys per the
    Web Push protocol (endpoint, keys.p256dh, keys.auth).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    subscription_json: str
    enabled: bool = Field(default=True, index=True)
    created_at: datetime | None = Field(default_factory=now_utc)

class ItemLink(SQLModel, table=True):
    """Directed links from a source item (todo or list) to a target item (todo or list).

    src_type/tgt_type: 'todo' or 'list'
    src_id/tgt_id: integer database ids
    label: optional human-friendly label for the link display
    position: optional ordering per (src_type, src_id)
    owner_id: owner of the source item (enforced by app-level checks)
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    src_type: str = Field(index=True)
    src_id: int = Field(index=True)
    tgt_type: str
    tgt_id: int
    label: Optional[str] = None
    position: Optional[int] = Field(default=None, index=True)
    owner_id: int = Field(foreign_key='user.id', index=True)
    created_at: datetime | None = Field(default_factory=now_utc)

    __table_args__ = (
        UniqueConstraint('src_type', 'src_id', 'tgt_type', 'tgt_id', name='uq_itemlink_edge'),
    )


class UserCollation(SQLModel, table=True):
    """Per-user collation list memberships with an active toggle.

    Each row indicates that `list_id` is one of the user's collations. When
    `active` is true, the UI should display inclusion indicators on todo pages
    for this list. Linking a todo into a collation uses ItemLink rows with
    src_type='list', src_id=list_id, tgt_type='todo'.
    """
    user_id: Optional[int] = Field(default=None, foreign_key='user.id', primary_key=True)
    list_id: Optional[int] = Field(default=None, foreign_key='liststate.id', primary_key=True)
    active: bool = Field(default=True, index=True)
    created_at: datetime | None = Field(default_factory=now_utc)


class UserListPrefs(SQLModel, table=True):
    """Per-user, per-list UI preferences.

    Composite primary key ensures a single row per (user, list).
    Currently stores the 'completed after' toggle for list.html.
    """
    user_id: Optional[int] = Field(default=None, foreign_key='user.id', primary_key=True)
    list_id: Optional[int] = Field(default=None, foreign_key='liststate.id', primary_key=True)
    completed_after: bool = Field(default=False, index=True)
    created_at: datetime | None = Field(default_factory=now_utc)
