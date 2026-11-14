"""In-memory fake StorageBackend for tests; never accesses a disk or network."""
from __future__ import annotations
from storage.storage_backend import StorageBackend


class IsolatedMemoryStorage(StorageBackend):
    def __init__(self):
        self.rows = []

    @property
    def location(self) -> str:
        return ':memory:'

    def load(self) -> list[dict]:
        return [dict(row) for row in self.rows]

    def append(self, row: dict) -> None:
        self.rows.append(dict(row))

    def replace(self, record_id: str, row: dict) -> None:
        for index, existing in enumerate(self.rows):
            if existing.get('record_id') == record_id:
                self.rows[index] = dict(row)
                return
        self.rows.append(dict(row))
