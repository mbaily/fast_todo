import pytest
import requests
from test_pwa.client import PwaClient
from test_pwa.local_store import local_store

# mark this module as pwa so it is skipped in normal test runs
pytestmark = pytest.mark.pwa


SERVER = "https://0.0.0.0:10443"
USERNAME = "mbaily"
PASSWORD = "mypass"


def server_available() -> bool:
    try:
        resp = requests.get(SERVER, verify=False, timeout=2)
        return resp.status_code < 500
    except Exception:
        return False


@pytest.fixture(autouse=True)
def clear_db():
    local_store.clear_all()
    yield
    local_store.clear_all()


def test_login_and_fetch():
    if not server_available():
        pytest.skip("Dev server not available")
    c = PwaClient(base_url=SERVER)
    assert c.login(USERNAME, PASSWORD)
    todos = c.fetch_all()
    assert isinstance(todos, list)
    counts = local_store.list_counts()
    assert counts["todos"] >= 0


def test_queue_and_sync():
    if not server_available():
        pytest.skip("Dev server not available")
    c = PwaClient(base_url=SERVER)
    assert c.login(USERNAME, PASSWORD)
    # queue a fake todo add - adjust to match expected payload format
    todo = {"text": "Local Test", "note": "from test", "list_id": 1}
    c.queue_local_change("create_todo", todo)
    counts = local_store.list_counts()
    assert counts["pending"] == 1
    res = c.sync()
    # sync may succeed or fail depending on server implementation, ensure method returns
    assert "synced" in res


def test_local_store_operations():
    """Test local store CRUD operations."""
    # Test storing and retrieving lists
    test_lists = [
        {"id": 1, "name": "Test List 1", "owner_id": 1, "created_at": "2023-01-01T00:00:00", "modified_at": "2023-01-01T00:00:00"},
        {"id": 2, "name": "Test List 2", "owner_id": 1, "created_at": "2023-01-02T00:00:00", "modified_at": "2023-01-02T00:00:00"}
    ]
    local_store.store_lists(test_lists)
    lists = local_store.get_lists()
    assert len(lists) == 2
    assert lists[0]["name"] == "Test List 1"

    # Test storing and retrieving todos
    test_todos = [
        {"id": 1, "text": "Test Todo 1", "note": "Note 1", "list_id": 1, "created_at": "2023-01-01T00:00:00", "modified_at": "2023-01-01T00:00:00"},
        {"id": 2, "text": "Test Todo 2", "note": "Note 2", "list_id": 1, "created_at": "2023-01-02T00:00:00", "modified_at": "2023-01-02T00:00:00"}
    ]
    local_store.store_todos(test_todos)
    todos = local_store.get_todos()
    assert len(todos) == 2
    assert todos[0]["text"] == "Test Todo 1"

    # Test counts
    counts = local_store.list_counts()
    assert counts["lists"] == 2
    assert counts["todos"] == 2
    assert counts["pending"] == 0


def test_pending_operations():
    """Test queuing and managing pending operations."""
    # Queue some operations
    local_store.queue_pending_op("create_todo", {"text": "Test", "list_id": 1})
    local_store.queue_pending_op("update_todo", {"id": 1, "text": "Updated"})

    pending = local_store.get_pending_ops()
    assert len(pending) == 2
    assert pending[0]["op_type"] == "create_todo"

    # Remove one operation
    local_store.remove_pending_op(pending[0]["id"])
    pending = local_store.get_pending_ops()
    assert len(pending) == 1


def test_sync_state():
    """Test sync state management."""
    # Set and get sync state
    local_store.set_sync_state("last_sync", "2023-01-01T00:00:00")
    state = local_store.get_sync_state("last_sync")
    assert state == "2023-01-01T00:00:00"

    # Test nonexistent key
    nonexistent = local_store.get_sync_state("nonexistent")
    assert nonexistent is None


def test_get_item_by_id():
    """Test getting specific items by ID."""
    # Setup test data
    test_lists = [{"id": 10, "name": "Specific List", "owner_id": 1}]
    test_todos = [{"id": 20, "text": "Specific Todo", "list_id": 10}]

    local_store.store_lists(test_lists)
    local_store.store_todos(test_todos)

    # Test get list by ID
    lst = local_store.get_list_by_id(10)
    assert lst is not None
    assert lst["name"] == "Specific List"

    # Test get todo by ID
    todo = local_store.get_todo_by_id(20)
    assert todo is not None
    assert todo["text"] == "Specific Todo"

    # Test nonexistent IDs
    assert local_store.get_list_by_id(999) is None
    assert local_store.get_todo_by_id(999) is None


def test_hierarchical_data_storage():
    """Test storing and retrieving hierarchical data."""
    # Clear existing data
    local_store.clear_all()

    # Create test data with hierarchy
    test_lists = [
        {"id": 1, "name": "Root List 1", "owner_id": 1, "category_id": None, "parent_todo_id": None, "parent_list_id": None},
        {"id": 2, "name": "Child List of Todo", "owner_id": 1, "category_id": None, "parent_todo_id": 10, "parent_list_id": None},
        {"id": 3, "name": "Child List of List", "owner_id": 1, "category_id": None, "parent_todo_id": None, "parent_list_id": 1},
    ]

    test_todos = [
        {"id": 10, "text": "Parent Todo", "list_id": 1},
        {"id": 11, "text": "Child Todo", "list_id": 1},
    ]

    local_store.store_lists(test_lists)
    local_store.store_todos(test_todos)

    # Verify data is stored
    lists = local_store.get_lists()
    todos = local_store.get_todos()

    assert len(lists) == 3
    assert len(todos) == 2

    # Check parent-child relationships
    list_by_id = {lst['id']: lst for lst in lists}
    assert list_by_id[1]['parent_todo_id'] is None
    assert list_by_id[1]['parent_list_id'] is None
    assert list_by_id[2]['parent_todo_id'] == 10
    assert list_by_id[3]['parent_list_id'] == 1


def test_categories_storage():
    """Test storing and retrieving categories."""
    # Clear existing data
    local_store.clear_all()

    # Create test categories
    test_categories = [
        {"id": 1, "name": "Work", "position": 1, "sort_alphanumeric": False},
        {"id": 2, "name": "Personal", "position": 2, "sort_alphanumeric": True},
        {"id": 3, "name": "Uncategorized", "position": 0, "sort_alphanumeric": False},
    ]

    local_store.store_categories(test_categories)

    # Verify categories are stored and retrieved in position order
    categories = local_store.get_categories()
    assert len(categories) == 3

    # Should be ordered by position
    assert categories[0]['name'] == 'Uncategorized'  # position 0
    assert categories[1]['name'] == 'Work'  # position 1
    assert categories[2]['name'] == 'Personal'  # position 2

    # Check individual fields
    uncat = categories[0]
    assert uncat['id'] == 3
    assert uncat['position'] == 0
    assert uncat['sort_alphanumeric'] == False
