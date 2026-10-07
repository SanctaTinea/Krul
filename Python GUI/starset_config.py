"""Persistent per-device profiles for Starset user settings."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import QStandardPaths


CONFIG_VERSION = 1


def default_config_path() -> Path:
    root = QStandardPaths.writableLocation(QStandardPaths.GenericConfigLocation)
    base = Path(root) if root else Path.home() / ".config"
    return base / "Starset" / "profiles.json"


class DeviceProfileStore:
    """Versioned JSON profiles selected using a WHOAMI response."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_config_path()
        self.data: dict[str, Any] = {
            "version": CONFIG_VERSION, "devices": {}, "settings": {}
        }
        self.active_key: str | None = None
        self.dirty = False
        self._load()

    @staticmethod
    def profile_key(identity: dict[str, Any]) -> str:
        device_name = str(identity.get("device_name") or "unknown").strip()
        device_id = str(identity.get("device_id") or "").strip()
        if device_id:
            return f"device:{device_name}:{device_id}"
        protocol = identity.get("protocol_version", "unknown")
        return f"model:{device_name}:protocol:{protocol}"

    def _load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if (
            isinstance(loaded, dict)
            and loaded.get("version") == CONFIG_VERSION
            and isinstance(loaded.get("devices"), dict)
        ):
            self.data = loaded

    def select_device(self, identity: dict[str, Any]) -> str:
        key = self.profile_key(identity)
        self.active_key = key
        devices = self.data["devices"]
        profile = devices.setdefault(key, {})
        if not isinstance(profile, dict):
            profile = {}
            devices[key] = profile
        profile["identity"] = {
            name: identity[name]
            for name in ("device_name", "device_id", "firmware", "protocol_version")
            if name in identity
        }
        profile.setdefault("widgets", {})
        profile.setdefault("graphs", {})
        return key

    def section(self, name: str) -> dict[str, Any]:
        if self.active_key is None:
            return {}
        profile = self.data["devices"].get(self.active_key, {})
        value = profile.get(name, {}) if isinstance(profile, dict) else {}
        return deepcopy(value) if isinstance(value, dict) else {}

    def settings(self) -> dict[str, Any]:
        """Return device-independent UI settings shared by all profiles."""

        value = self.data.get("settings", {})
        return deepcopy(value) if isinstance(value, dict) else {}

    def set_settings(self, value: dict[str, Any]) -> None:
        self.data["settings"] = deepcopy(value)
        self.dirty = True

    def set_section(self, name: str, value: dict[str, Any]) -> None:
        if self.active_key is None:
            return
        profile = self.data["devices"].setdefault(self.active_key, {})
        profile[name] = deepcopy(value)
        self.dirty = True

    def flush(self) -> None:
        if not self.dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            payload = json.dumps(
                self.data, ensure_ascii=False, indent=2, sort_keys=True,
            ) + "\n"
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            return
        self.dirty = False
