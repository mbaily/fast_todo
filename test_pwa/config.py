"""Configuration management for test_pwa client."""

import os
import json
from typing import Dict, Any
from pathlib import Path


class Config:
    """Configuration manager for the test_pwa client."""

    def __init__(self, config_file: str = None):
        self.config_file = config_file or os.path.join(
            os.path.dirname(__file__), 'config.json'
        )
        self._config: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Load configuration from file."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    self._config = json.load(f)
            except Exception:
                # If file is corrupted, start with empty config
                self._config = {}
        else:
            # Default configuration
            self._config = {
                'server_url': 'https://0.0.0.0:10443',
                'username': 'mbaily',
                'password': 'mypass'
            }
            self.save()

    def save(self) -> None:
        """Save configuration to file."""
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
        with open(self.config_file, 'w') as f:
            json.dump(self._config, f, indent=2)

    @property
    def server_url(self) -> str:
        return self._config.get('server_url', 'https://0.0.0.0:10443')

    @server_url.setter
    def server_url(self, value: str):
        self._config['server_url'] = value
        self.save()

    @property
    def username(self) -> str:
        return self._config.get('username', 'mbaily')

    @username.setter
    def username(self, value: str):
        self._config['username'] = value
        self.save()

    @property
    def password(self) -> str:
        return self._config.get('password', 'mypass')

    @password.setter
    def password(self, value: str):
        self._config['password'] = value
        self.save()


# Global config instance
config = Config()
