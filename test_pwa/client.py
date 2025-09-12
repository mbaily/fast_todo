"""PWA client for communicating with the server API."""

import requests
import json
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime
import ssl
import urllib3

try:
    from local_store import local_store
    from config import config
except ImportError:
    from .local_store import local_store
    from . import config


class PwaClient:
    """Client for PWA API operations."""

    def __init__(self, base_url: str = None):
        self.base_url = base_url or config.server_url
        self.session_token: Optional[str] = None
        self.access_token: Optional[str] = None
        self.csrf_token: Optional[str] = None
        self.session = requests.Session()

        # Disable SSL verification for self-signed certs
        self.session.verify = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def login(self, username: str, password: str) -> bool:
        """Login to the server and store tokens."""
        try:
            url = f"{self.base_url}/html_tailwind/login"
            payload = {"username": username, "password": password}
            headers = {"Content-Type": "application/json"}

            response = self.session.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    self.session_token = data.get('session_token')
                    self.access_token = data.get('access_token')
                    self.csrf_token = data.get('csrf_token')
                    return True
            return False
        except Exception:
            return False

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for requests."""
        headers = {}
        if self.session_token:
            headers['Cookie'] = f'session_token={self.session_token}'
        if self.access_token:
            headers['Authorization'] = f'Bearer {self.access_token}'
        if self.csrf_token:
            headers['X-CSRF-Token'] = self.csrf_token
        return headers

    def sync_get(self, since: Optional[str] = None) -> Dict[str, Any]:
        """Get sync data from server."""
        try:
            url = f"{self.base_url}/sync"
            params = {}
            if since:
                params['since'] = since

            response = self.session.get(url, headers=self._get_auth_headers(), params=params)
            if response.status_code == 200:
                return response.json()
            return {}
        except Exception:
            return {}

    def sync_post(self, ops: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Post sync operations to server."""
        try:
            url = f"{self.base_url}/sync"
            payload = {"ops": ops}

            response = self.session.post(
                url,
                json=payload,
                headers={**self._get_auth_headers(), "Content-Type": "application/json"}
            )
            if response.status_code == 200:
                return response.json()
            return {"results": []}
        except Exception:
            return {"results": []}

    def fetch_all(self) -> List[Dict[str, Any]]:
        """Fetch all data and store locally."""
        data = self.sync_get()
        if 'lists' in data:
            local_store.store_lists(data['lists'])
        if 'todos' in data:
            local_store.store_todos(data['todos'])
        if 'categories' in data:
            local_store.store_categories(data['categories'])
        # Store last sync time
        if 'server_ts' in data:
            local_store.set_sync_state('last_sync', data['server_ts'])
        return data.get('todos', [])

    def sync_pending_changes(self) -> Dict[str, Any]:
        """Sync all pending local changes to server."""
        pending = local_store.get_pending_ops()
        if not pending:
            return {"synced": 0, "results": []}

        ops = []
        for op in pending:
            op_data = op['data'].copy()
            op_data['op_id'] = str(uuid.uuid4())
            ops.append({
                'op': op['op_type'],
                'payload': op_data
            })

        results = self.sync_post(ops)
        synced_count = 0

        # Process results and remove successful operations
        for i, result in enumerate(results.get('results', [])):
            if result.get('status') == 'ok':
                local_store.remove_pending_op(pending[i]['id'])
                synced_count += 1
            elif result.get('status') == 'conflict':
                # Handle conflict - for now, just log and keep pending
                print(f"Conflict detected for operation {pending[i]['op_type']}: {result}")

        return {"synced": synced_count, "results": results.get('results', [])}

    def queue_local_change(self, op_type: str, data: Dict[str, Any]) -> None:
        """Queue a local change for later sync."""
        local_store.queue_pending_op(op_type, data)

    def sync(self) -> Dict[str, Any]:
        """Perform full sync: fetch updates and push pending changes."""
        # First push pending changes
        push_result = self.sync_pending_changes()

        # Then fetch latest data
        last_sync = local_store.get_sync_state('last_sync')
        fetch_result = self.sync_get(since=last_sync)

        if 'lists' in fetch_result:
            local_store.store_lists(fetch_result['lists'])
        if 'todos' in fetch_result:
            local_store.store_todos(fetch_result['todos'])
        if 'server_ts' in fetch_result:
            local_store.set_sync_state('last_sync', fetch_result['server_ts'])

        return {
            "push": push_result,
            "fetch": fetch_result,
            "synced": push_result.get('synced', 0)
        }
