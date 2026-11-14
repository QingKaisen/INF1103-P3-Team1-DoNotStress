"""OOP persistence manager for student check-ins.

Domain validation, privacy filtering, opt-in requirements, query and record
lifecycle belong to DataManager. Concrete JSON I/O belongs to JSONStorage.
Legacy function entry points at the end of this module remain as adapters for
Phase 1 callers; production injects a DataManager into WebController.
"""

from __future__ import annotations

import uuid
import os  # compatibility: older tests patch os.replace via data_manager.os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from base_manager import BaseManager
from json_types import JSONObject
from storage.json_storage import JSONStorage
from storage.storage_backend import StorageBackend
from storage.storage_error import StorageError

_DEFAULT_DATA_PATH = Path(__file__).resolve().parent / "data" / "student_records.json"

_BLOCKED_SOURCES = frozenset({"logic_fallback", "fallback", "logic_only", "logic"})
_AI_SUCCESS_SOURCES = frozenset({"gemini", "ai", "ai_manager", "gemini+logic", "ai_logic"})
TIER_TO_CATEGORY = {
    "You're doing ok": "Low",
    "Worth a check-in": "Moderate",
    "Please reach out": "High",
}
_REQUIRED_AI_FIELDS = (
    "risk_score", "risk_category", "primary_stressors",
    "recommended_support", "confidence", "reasoning",
)


class DataManager(BaseManager):
    """Validate, persist and query records through a StorageBackend contract.

    The manager owns domain rules but does not know the JSON file format.
    A different backend can be injected without changing business behaviour.
    The supplied backend is an independent aggregation, not owned by the
    manager; the default JSONStorage is constructed here (composition).
    """

    def __init__(self, data_path: str | Path | None = None,
                 *, backend: StorageBackend | None = None) -> None:
        super().__init__("data_manager")
        if backend is not None and not isinstance(backend, StorageBackend):
            raise TypeError("backend must implement StorageBackend")
        self.__backend: StorageBackend = (backend if backend is not None
                                          else JSONStorage(self._resolve_path(data_path)))

    @property
    def backend(self) -> StorageBackend:
        """The abstract storage interface used for persistence."""
        return self.__backend

    def describe(self) -> JSONObject:
        return {"backend": type(self.__backend).__name__,
                "location": self.__backend.location, "format": "record dictionaries"}

    @staticmethod
    def get_default_data_path() -> str:
        return str(_DEFAULT_DATA_PATH)

    @staticmethod
    def _resolve_path(path: str | Path | None) -> Path:
        return Path(path) if path is not None and str(path).strip() else _DEFAULT_DATA_PATH

    @staticmethod
    def _is_opted_in(opt_in: Any) -> bool:
        if opt_in is True:
            return True
        return isinstance(opt_in, str) and opt_in.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _is_valid_probability(value: Any) -> bool:
        if isinstance(value, bool):
            return False
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return 0.0 <= number <= 1.0

    def _validate_ai_fields(self, record: dict[str, Any]) -> str | None:
        missing = [field for field in _REQUIRED_AI_FIELDS if field not in record]
        if missing:
            return "Save refused: AI fields missing: " + ", ".join(missing) + "."
        if not self._is_valid_probability(record["risk_score"]):
            return "Save refused: risk_score must be a float between 0.0 and 1.0."
        if not self._is_valid_probability(record["confidence"]):
            return "Save refused: confidence must be a float between 0.0 and 1.0."
        category = record["risk_category"]
        if not isinstance(category, str) or category not in {"Low", "Moderate", "High"}:
            return "Save refused: risk_category must be 'Low', 'Moderate', or 'High'."
        stressors = record["primary_stressors"]
        if not isinstance(stressors, list) or not all(isinstance(item, str) for item in stressors):
            return "Save refused: primary_stressors must be a list of strings."
        if not isinstance(record["recommended_support"], str):
            return "Save refused: recommended_support must be a string."
        if not isinstance(record["reasoning"], str):
            return "Save refused: reasoning must be a string."
        return None

    def _ai_success_error(self, record: dict[str, Any]) -> str | None:
        source = str(record.get("source", "")).strip().lower()
        if source in _BLOCKED_SOURCES:
            return f"Save refused: record is not AI-processed (source={record.get('source')!r})."
        if record.get("ai_ok") is not True and source not in _AI_SUCCESS_SOURCES:
            return ("Save refused: successful Gemini processing required "
                    "(set source to 'gemini' or ai_ok=True).")
        return self._validate_ai_fields(record)

    @staticmethod
    def _stored_copy(record: dict[str, Any], *, pending: bool,
                     record_id: str | None = None) -> dict[str, Any]:
        stored = dict(record)
        stored.pop("feelings_text", None)
        stored.pop("reflection", None)
        stored["save_opt_in"] = True
        if pending:
            stored["status"] = "pending"
            stored["ai_ok"] = False
        else:
            stored.pop("status", None)
        if not stored.get("saved_at"):
            stored["saved_at"] = datetime.now(timezone.utc).isoformat()
        candidate = record_id if record_id is not None else stored.get("record_id")
        stored["record_id"] = (candidate.strip() if isinstance(candidate, str) and candidate.strip()
                               else uuid.uuid4().hex)
        return stored

    def _response(self, *, record: dict[str, Any] | None = None,
                  error: str | None = None) -> dict[str, Any]:
        return {"ok": error is None, "error": error,
                "path": self.__backend.location, "record": record}

    def _write(self, stored: dict[str, Any], *, replace_id: str | None = None) -> dict[str, Any]:
        try:
            if replace_id is None:
                self.__backend.append(stored)
            else:
                self.__backend.replace(replace_id, stored)
        except (StorageError, OSError, ValueError, TypeError) as exc:
            self.logger.warning("Record storage failed: %s", type(exc).__name__)
            return self._response(error=str(exc))
        return self._response(record=stored)

    def load_all_records(self) -> dict[str, Any]:
        try:
            rows = self.__backend.load()
        except (StorageError, OSError, ValueError, TypeError) as exc:
            return {"ok": False, "records": [], "error": str(exc),
                    "path": self.__backend.location}
        return {"ok": True, "records": rows, "error": None,
                "path": self.__backend.location}

    def save_record(self, record: dict[str, Any], *, opt_in: Any = False) -> dict[str, Any]:
        """Append only evaluated AI records after opt-in and validation."""
        if not self._is_opted_in(opt_in):
            return self._response(error="Save refused: student opt-in required (pass opt_in=True).")
        if not isinstance(record, dict):
            return self._response(error="record must be a dict")
        error = self._ai_success_error(record)
        if error:
            return self._response(error=error)
        return self._write(self._stored_copy(record, pending=False))

    def save_pending_record(self, record: dict[str, Any], *, opt_in: Any = False) -> dict[str, Any]:
        """Store answers as pending when mandatory AI processing failed."""
        if not self._is_opted_in(opt_in):
            return self._response(error="Save refused: student opt-in required (pass opt_in=True).")
        if not isinstance(record, dict):
            return self._response(error="record must be a dict")
        return self._write(self._stored_copy(record, pending=True))

    def replace_pending_record(self, record_id: str, new_record: dict[str, Any],
                               *, opt_in: Any = False) -> dict[str, Any]:
        """Upgrade a pending row, or append when absent, preserving its ID."""
        clean_id = record_id.strip() if isinstance(record_id, str) else ""
        if not clean_id:
            return self._response(error="record_id is required")
        if not self._is_opted_in(opt_in):
            return self._response(error="Save refused: student opt-in required (pass opt_in=True).")
        if not isinstance(new_record, dict):
            return self._response(error="record must be a dict")
        error = self._ai_success_error(new_record)
        if error:
            return self._response(error=error)
        stored = self._stored_copy(new_record, pending=False, record_id=clean_id)
        return self._write(stored, replace_id=clean_id)

    def save(self, record: dict[str, Any], *, opt_in: Any = False) -> dict[str, Any]:
        return self.save_record(record, opt_in=opt_in)

    def load(self) -> list[dict[str, Any]]:
        return self.load_all_records()["records"]

    def query(self, filter_fn: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
        return [record for record in self.load() if filter_fn(record)]

    def filter_records(self, records: list[dict[str, Any]], **filters: Any) -> list[dict[str, Any]]:
        """Filter on student ID, risk tier and cohort year (pure domain logic)."""
        if not isinstance(records, list):
            return []
        student_id = filters.get("student_id")
        risk_category = filters.get("risk_category")
        tier = filters.get("tier")
        cohort_year = filters.get("cohort_year")
        results = []
        for record in records:
            if not isinstance(record, dict):
                continue
            if student_id is not None and str(record.get("student_id", "")) != str(student_id):
                continue
            if risk_category is not None and str(record.get("risk_category", "")).lower() != str(risk_category).lower():
                continue
            if tier is not None:
                label = str(record.get("soft_label") or "")
                category = str(record.get("risk_category") or "")
                if label != str(tier) and category != TIER_TO_CATEGORY.get(str(tier)):
                    continue
            if cohort_year is not None:
                prefix = str(cohort_year).zfill(2)[-2:]
                sid = str(record.get("student_id", ""))
                if len(sid) < 2 or sid[:2] != prefix:
                    continue
            results.append(record)
        return results


# Compatibility facade for the initial functional API; it is NOT used by
# application services or Flask routes. Legacy tests can still import it.
def get_default_data_path() -> str:
    return DataManager.get_default_data_path()


def load_all_records(data_path: str | None = None) -> dict[str, Any]:
    return DataManager(data_path).load_all_records()


def save_record(record: dict[str, Any], data_path: str | None = None,
                *, opt_in: Any = False) -> dict[str, Any]:
    return DataManager(data_path).save_record(record, opt_in=opt_in)


def save_pending_record(record: dict[str, Any], data_path: str | None = None,
                        *, opt_in: Any = False) -> dict[str, Any]:
    return DataManager(data_path).save_pending_record(record, opt_in=opt_in)


def replace_pending_record(record_id: str, new_record: dict[str, Any],
                           data_path: str | None = None, *, opt_in: Any = False) -> dict[str, Any]:
    return DataManager(data_path).replace_pending_record(record_id, new_record, opt_in=opt_in)


def filter_records(records: list[dict[str, Any]], **filters: Any) -> list[dict[str, Any]]:
    return DataManager().filter_records(records, **filters)


def save(record: dict[str, Any], data_path: str | None = None,
         *, opt_in: Any = False) -> dict[str, Any]:
    return DataManager(data_path).save(record, opt_in=opt_in)


def load(data_path: str | None = None) -> list[dict[str, Any]]:
    return DataManager(data_path).load()


def query(filter_fn: Callable[[dict[str, Any]], Any],
          data_path: str | None = None) -> list[dict[str, Any]]:
    return DataManager(data_path).query(filter_fn)
