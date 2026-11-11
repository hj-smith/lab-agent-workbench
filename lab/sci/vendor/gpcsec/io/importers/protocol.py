"""Importer plugin protocol."""

from __future__ import annotations

from typing import Protocol

from gpcsec.models import ImportResult


class ChromatogramImporter(Protocol):
    extensions: tuple[str, ...]

    def import_bytes(self, raw: bytes, source_name: str) -> ImportResult: ...
