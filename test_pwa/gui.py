"""TK GUI for the PWA test client."""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog
import threading
import time
import json
from typing import List, Dict, Any, Optional

try:
    from local_store import local_store
    from config import config
    from client import PwaClient
except ImportError:
    from .local_store import local_store
    from . import config
    from .client import PwaClient


class PwaGui:
    """Main TK GUI for PWA client."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PWA Test Client")
        self.root.geometry("1000x700")

        self.client = PwaClient()
        self.current_selection: Optional[Dict[str, Any]] = None
        self.log_messages: List[str] = []

        self._setup_ui()
        self._setup_bindings()
        self._start_background_sync()

    def _setup_ui(self):
        """Setup the main UI components."""
        # Main frame
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Top controls
        controls_frame = ttk.Frame(main_frame)
        controls_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Button(controls_frame, text="Login", command=self._login).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(controls_frame, text="Sync", command=self._manual_sync).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(controls_frame, text="Clear Data", command=self._clear_data).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(controls_frame, text="Settings", command=self._show_settings).pack(side=tk.LEFT, padx=(0, 5))

        # Search frame
        search_frame = ttk.Frame(controls_frame)
        search_frame.pack(side=tk.RIGHT)
        ttk.Label(search_frame, text="Search ID:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=15)
        search_entry.pack(side=tk.LEFT, padx=(5, 0))
        search_entry.bind('<Return>', lambda e: self._search_by_id())

        # Paned window for main content
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Left panel - Tree view
        left_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=1)

        # Tree view for data
        self.tree = ttk.Treeview(left_frame)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.heading('#0', text='Items')

        # Configure tree columns
        self.tree['columns'] = ('type', 'completed')
        self.tree.heading('type', text='Type')
        self.tree.heading('completed', text='Completed')
        self.tree.column('type', width=80)
        self.tree.column('completed', width=80)

        # Right panel - Log
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)

        ttk.Label(right_frame, text="Activity Log").pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(right_frame, height=20)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Status bar
        self.status_var = tk.StringVar()
        self.status_var.set("Not logged in")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN)
        status_bar.pack(fill=tk.X, pady=(10, 0))

        # Initial data load
        self._refresh_tree()

    def _setup_bindings(self):
        """Setup keyboard and mouse bindings."""
        self.root.bind('<Control-f>', lambda e: self._focus_search())
        self.root.bind('<Double-Button-1>', lambda e: self._show_details())
        self.tree.bind('<<TreeviewSelect>>', self._on_tree_select)

    def _focus_search(self):
        """Focus the search entry."""
        # Find the search entry and focus it
        for widget in self.root.winfo_children():
            if isinstance(widget, ttk.Frame):
                for subwidget in widget.winfo_children():
                    if isinstance(subwidget, ttk.Frame):
                        for subsub in subwidget.winfo_children():
                            if isinstance(subsub, ttk.Entry):
                                subsub.focus()
                                return

    def _search_by_id(self):
        """Search for item by ID."""
        search_id = self.search_var.get().strip()
        if not search_id:
            return

        try:
            item_id = int(search_id)
            # Search in lists
            list_item = local_store.get_list_by_id(item_id)
            if list_item:
                self._select_item_in_tree(f"list_{item_id}")
                return

            # Search in todos
            todo_item = local_store.get_todo_by_id(item_id)
            if todo_item:
                self._select_item_in_tree(f"todo_{item_id}")
                return

            messagebox.showinfo("Not Found", f"No item found with ID {item_id}")

        except ValueError:
            messagebox.showerror("Invalid ID", "Please enter a valid numeric ID")

    def _select_item_in_tree(self, item_id: str):
        """Select an item in the tree view."""
        for item in self.tree.get_children():
            if self.tree.item(item, 'values')[0] == item_id:
                self.tree.selection_set(item)
                self.tree.see(item)
                break

    def _on_tree_select(self, event):
        """Handle tree selection."""
        selection = self.tree.selection()
        if selection:
            item = selection[0]
            item_type = self.tree.item(item, 'values')[0]
            if item_type.startswith('list_'):
                list_id = int(item_type.split('_')[1])
                self.current_selection = local_store.get_list_by_id(list_id)
                self.current_selection['item_type'] = 'list'
            elif item_type.startswith('todo_'):
                todo_id = int(item_type.split('_')[1])
                self.current_selection = local_store.get_todo_by_id(todo_id)
                self.current_selection['item_type'] = 'todo'

    def _show_details(self):
        """Show full details of selected item."""
        if not self.current_selection:
            return

        item = self.current_selection
        details = json.dumps(item, indent=2, default=str)

        # Create popup window
        popup = tk.Toplevel(self.root)
        popup.title(f"Details - {item.get('item_type', 'Unknown')} {item.get('id', 'N/A')}")
        popup.geometry("600x400")

        text = scrolledtext.ScrolledText(popup)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(tk.END, details)
        text.config(state=tk.DISABLED)

    def _login(self):
        """Perform login."""
        def login_thread():
            self._log("Attempting login...")
            username = config.username
            password = config.password
            if self.client.login(username, password):
                self._log("Login successful")
                self.status_var.set(f"Logged in as {username}")
                self._fetch_data()
            else:
                self._log("Login failed")
                self.status_var.set("Login failed")

        threading.Thread(target=login_thread, daemon=True).start()

    def _manual_sync(self):
        """Perform manual sync."""
        def sync_thread():
            self._log("Starting manual sync...")
            result = self.client.sync()
            synced = result.get('synced', 0)
            self._log(f"Sync completed: {synced} operations synced")
            self._refresh_tree()

        threading.Thread(target=sync_thread, daemon=True).start()

    def _clear_data(self):
        """Clear all local data."""
        if messagebox.askyesno("Confirm", "Clear all local data?"):
            local_store.clear_all()
            self._log("Local data cleared")
            self._refresh_tree()

    def _show_settings(self):
        """Show settings dialog."""
        settings = tk.Toplevel(self.root)
        settings.title("Settings")
        settings.geometry("400x200")

        # Server URL
        ttk.Label(settings, text="Server URL:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        url_var = tk.StringVar(value=config.server_url)
        ttk.Entry(settings, textvariable=url_var).grid(row=0, column=1, sticky=tk.EW, padx=5, pady=5)

        # Username
        ttk.Label(settings, text="Username:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        user_var = tk.StringVar(value=config.username)
        ttk.Entry(settings, textvariable=user_var).grid(row=1, column=1, sticky=tk.EW, padx=5, pady=5)

        # Password
        ttk.Label(settings, text="Password:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        pass_var = tk.StringVar(value=config.password)
        ttk.Entry(settings, textvariable=pass_var, show="*").grid(row=2, column=1, sticky=tk.EW, padx=5, pady=5)

        def save_settings():
            config.server_url = url_var.get()
            config.username = user_var.get()
            config.password = pass_var.get()
            self._log("Settings saved")
            settings.destroy()

        ttk.Button(settings, text="Save", command=save_settings).grid(row=3, column=0, columnspan=2, pady=10)
        settings.grid_columnconfigure(1, weight=1)

    def _fetch_data(self):
        """Fetch initial data."""
        def fetch_thread():
            self._log("Fetching data...")
            self.client.fetch_all()
            self._log("Data fetched")
            self._refresh_tree()

        threading.Thread(target=fetch_thread, daemon=True).start()

    def _refresh_tree(self):
        """Refresh the tree view with current data."""
        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Get all data
        lists = local_store.get_lists()
        todos = local_store.get_todos()  # Get all todos
        categories = local_store.get_categories()

        # Create lookup dictionaries
        list_by_id = {lst['id']: lst for lst in lists}
        todos_by_list_id = {}
        for todo in todos:
            list_id = todo['list_id']
            if list_id not in todos_by_list_id:
                todos_by_list_id[list_id] = []
            todos_by_list_id[list_id].append(todo)

        # Create category lookup
        category_by_id = {cat['id']: cat for cat in categories}
        category_by_name = {cat['name']: cat for cat in categories}

        # Build hierarchy: find root items (no parents)
        root_lists = []
        child_lists = set()  # Lists that are children of other items

        for lst in lists:
            if lst.get('parent_todo_id') or lst.get('parent_list_id'):
                child_lists.add(lst['id'])
            else:
                root_lists.append(lst)

        # Group root lists by category
        categorized_lists = {}
        uncategorized_lists = []

        for lst in root_lists:
            cat_id = lst.get('category_id')
            if cat_id and cat_id in category_by_id:
                if cat_id not in categorized_lists:
                    categorized_lists[cat_id] = []
                categorized_lists[cat_id].append(lst)
            else:
                uncategorized_lists.append(lst)

        # Add categories in position order
        sorted_categories = sorted(categories, key=lambda c: c['position'])

        for cat in sorted_categories:
            cat_node = self.tree.insert('', 'end', text=cat['name'], values=('category', ''))
            # Add lists for this category
            cat_lists = categorized_lists.get(cat['id'], [])
            for lst in cat_lists:
                self._add_hierarchical_node(cat_node, lst, list_by_id, todos_by_list_id, child_lists)

        # Handle uncategorized lists
        if uncategorized_lists:
            # Check if there's an "Uncategorized" category
            uncat_category = category_by_name.get('Uncategorized')
            if uncat_category:
                # Use the existing "Uncategorized" category
                cat_node = self.tree.insert('', 'end', text=uncat_category['name'], values=('category', ''))
                for lst in uncategorized_lists:
                    self._add_hierarchical_node(cat_node, lst, list_by_id, todos_by_list_id, child_lists)
            else:
                # Create synthetic "Uncategorized" category
                cat_node = self.tree.insert('', 'end', text='Uncategorized', values=('category', ''))
                for lst in uncategorized_lists:
                    self._add_hierarchical_node(cat_node, lst, list_by_id, todos_by_list_id, child_lists)

        # Update counts
        counts = local_store.list_counts()
        self.status_var.set(f"Lists: {counts['lists']}, Todos: {counts['todos']}, Pending: {counts['pending']}")

    def _add_hierarchical_node(self, parent: str, item: Dict[str, Any], list_by_id: Dict[int, Dict], todos_by_list_id: Dict[int, List], child_lists: set):
        """Recursively add a node and its children to the tree."""
        if 'name' in item:  # This is a list
            list_node = self.tree.insert(parent, 'end',
                                       text=item['name'],
                                       values=(f"list_{item['id']}", 'list', item.get('completed', False)))

            # Add todos in this list (that aren't children of other items)
            list_todos = todos_by_list_id.get(item['id'], [])
            for todo in list_todos:
                self._add_hierarchical_node(list_node, todo, list_by_id, todos_by_list_id, child_lists)

            # Add child lists of this list
            for lst in list_by_id.values():
                if lst.get('parent_list_id') == item['id']:
                    self._add_hierarchical_node(list_node, lst, list_by_id, todos_by_list_id, child_lists)

        else:  # This is a todo
            todo_node = self.tree.insert(parent, 'end',
                                       text=item['text'][:50] + ('...' if len(item['text']) > 50 else ''),
                                       values=(f"todo_{item['id']}", 'todo', False))

            # Add child lists of this todo
            for lst in list_by_id.values():
                if lst.get('parent_todo_id') == item['id']:
                    self._add_hierarchical_node(todo_node, lst, list_by_id, todos_by_list_id, child_lists)

    def _log(self, message: str):
        """Add message to log."""
        timestamp = time.strftime("%H:%M:%S")
        full_message = f"[{timestamp}] {message}\n"
        self.log_messages.append(full_message)

        # Keep only last 100 messages
        if len(self.log_messages) > 100:
            self.log_messages = self.log_messages[-100:]

        # Update log display
        self.log_text.delete(1.0, tk.END)
        self.log_text.insert(tk.END, ''.join(self.log_messages))
        self.log_text.see(tk.END)

    def _start_background_sync(self):
        """Start background sync thread."""
        def sync_loop():
            while True:
                time.sleep(300)  # Sync every 5 minutes
                if hasattr(self.client, 'session_token') and self.client.session_token:
                    try:
                        result = self.client.sync()
                        synced = result.get('synced', 0)
                        if synced > 0:
                            self._log(f"Background sync: {synced} operations synced")
                            self.root.after(0, self._refresh_tree)
                    except Exception as e:
                        self._log(f"Background sync failed: {e}")

        threading.Thread(target=sync_loop, daemon=True).start()


def main():
    """Main entry point."""
    root = tk.Tk()
    app = PwaGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
