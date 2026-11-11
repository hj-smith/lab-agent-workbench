"""Opaque container state used only by persistence, carried through dataclass.replace."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ContainerState:
    manifest: dict[str, Any] = field(default_factory=dict)
    known_manifest: dict[str, Any] = field(default_factory=dict)
    members: dict[str, bytes] = field(default_factory=dict)
    arrays: dict[str, tuple[object, bytes]] = field(default_factory=dict)
    base_directory: Path | None = None
    copy_imports_to_attachments: bool = True
    attachment_stem: str | None = None
