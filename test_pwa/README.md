# PWA Test Client

A Python TK GUI client for testing the PWA (Progressive Web App) functionality of the fast_todo application. This client allows offline exploration of todo data with sync capabilities.

## Features

- **Offline Data Exploration**: Browse lists, categories, and todos without an internet connection
- **Automatic Sync**: Periodically syncs data with the server in the background
- **Manual Sync**: Trigger immediate sync operations
- **Conflict Resolution**: Handles sync conflicts with user prompts
- **Search Functionality**: Search for items by ID using Ctrl+F
- **Detailed Views**: Double-click items to see full details in popup windows
- **Activity Logging**: Real-time log of all operations and sync activity
- **Data Clearing**: Clear all local data with a single button
- **HTTPS Support**: Works with self-signed certificates

## Installation

1. Ensure you have Python 3.8+ installed
2. Install required dependencies:
   ```bash
   pip install requests
   ```
3. The client uses SQLite for local storage (no additional setup required)

## Configuration

The client can be configured via the `config.json` file in the `test_pwa/` directory:

```json
{
  "server_url": "https://0.0.0.0:10443",
  "username": "mbaily",
  "password": "mypass"
}
```

You can also modify these settings through the GUI by clicking the "Settings" button.

### Configuration Options

- **server_url**: The base URL of the fast_todo server
- **username**: Your login username
- **password**: Your login password

## Usage

### Starting the Client

```bash
cd test_pwa
python main.py
```

### Basic Workflow

1. **Login**: Click the "Login" button to authenticate with the server
2. **Initial Sync**: The client will automatically fetch your data after login
3. **Browse Data**: Use the tree view on the left to explore:
   - Categories containing lists
   - Lists containing todos
   - Individual todo items
4. **View Details**: Double-click any item to see its full details
5. **Search**: Press Ctrl+F to search for items by ID
6. **Manual Sync**: Click "Sync" to immediately sync pending changes
7. **Monitor Activity**: Check the log window for operation status

### Offline Operation

- The client works entirely offline once data is synced
- Changes made offline are queued for later sync
- When back online, pending changes are automatically synced

### Data Clearing

- Click "Clear Data" to remove all local data
- Useful for testing or starting fresh
- Requires confirmation to prevent accidental data loss

### Data Hierarchy

The GUI displays a complete hierarchical view of your data:

- **Categories**: Top-level organization from the database
  - Categories are shown in position order
  - Only lists belong under categories (no todos directly)
- **Lists under Categories**: Root lists that have a `category_id` appear under their respective categories
- **Uncategorized Lists**: Lists without a `category_id` appear under:
  - An "Uncategorized" category if it exists in the database
  - A synthetic "Uncategorized" category if no such category exists
- **List Children**: Todos belonging to each list, and any sublists that are children of the list
- **Todo Children**: Any sublists that are children of specific todos

The hierarchy supports:
- Lists as children of todos (via `parent_todo_id`)
- Lists as children of other lists (via `parent_list_id`)
- Todos as children of lists (standard relationship)

This creates a fully nested tree structure reflecting the complete parent-child relationships in your data.

## GUI Features

### Tree View
- Hierarchical display of categories → lists → todos
- Shows item type and completion status
- Expandable/collapsible nodes

### Activity Log
- Real-time logging of all operations
- Scrollable text area
- Shows timestamps for each operation

### Search
- Ctrl+F to focus search box
- Enter numeric ID to find specific items
- Highlights found items in the tree

### Details Popup
- Double-click any item for full details
- JSON-formatted display of all fields
- Shows complete data structure

## Development

### Running Tests

```bash
pytest tests/test_test_pwa_client.py
```

### Code Structure

- `client.py`: API client for server communication
- `local_store.py`: SQLite-based local data storage
- `gui.py`: TK GUI implementation
- `config.py`: Configuration management
- `main.py`: Application entry point

### Testing with Live Server

The unit tests include integration tests that require a running server. Set the `SERVER`, `USERNAME`, and `PASSWORD` variables in the test file to match your test environment.

## Requirements

- Python 3.8+
- tkinter (usually included with Python)
- requests library
- SQLite3 (included with Python)

## Security Notes

- Passwords are stored in plain text in the config file
- Uses HTTPS but accepts self-signed certificates for testing
- No encryption of local SQLite database
- Suitable only for development/testing environments
