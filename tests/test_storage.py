"""StorageBackend, JSONStorage, SaveResult and StorageError on their own."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plain_tests import capture_logs, case, function_suite
from storage.json_storage import JSONStorage
from storage.save_result import SaveResult
from storage.storage_backend import StorageBackend
from storage.storage_error import StorageError


class MemoryStorage(StorageBackend):
    """A backend that keeps rows in a list, written only for these tests."""

    def __init__(self):
        super().__init__()
        self.__rows = []

    @property
    def location(self):
        return "memory"

    def load(self):
        return [dict(row) for row in self.__rows]

    def append(self, row):
        self.__rows.append(dict(row))

    def replace(self, record_id, row):
        for index, existing in enumerate(self.__rows):
            if existing.get("record_id") == record_id:
                self.__rows[index] = dict(row)
                return
        self.__rows.append(dict(row))


def test_storage_backend_cannot_be_constructed():
    try:
        StorageBackend()
    except TypeError:
        return
    raise AssertionError("StorageBackend must be abstract")


def test_a_second_backend_can_implement_the_same_contract():
    """Any StorageBackend can load, append and replace rows the same way."""
    storage = MemoryStorage()
    assert isinstance(storage, StorageBackend)
    assert storage.location == "memory"
    assert storage.load() == []
    storage.append({"record_id": "a", "student_id": "2600001"})
    storage.append({"record_id": "b", "student_id": "2600002"})
    storage.replace("a", {"record_id": "a", "student_id": "2700001"})
    storage.replace("c", {"record_id": "c", "student_id": "2700003"})
    assert [row["student_id"] for row in storage.load()] == [
        "2700001", "2600002", "2700003"]
    storage.load().clear()
    assert len(storage.load()) == 3


def test_save_result_reports_the_stored_row_or_the_error():
    saved = SaveResult(row={"record_id": "abc123", "student_id": "2600001"})
    assert saved.ok and saved.error is None
    assert saved.record_id == "abc123"
    assert saved.row["student_id"] == "2600001"
    refused = SaveResult(error="disk full")
    assert not refused.ok and refused.row is None and refused.record_id == ""
    assert refused.error == "disk full"
    empty = SaveResult()
    assert not empty.ok and empty.record_id == ""


def test_a_missing_or_empty_file_is_no_records():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "records.json"
        storage = JSONStorage(path)
        assert storage.location == str(path)
        assert storage.load() == []
        path.write_text("  \n", encoding="utf-8")
        assert storage.load() == []
        path.write_text('{"records": [{"student_id": "2000001"}, 3]}',
                        encoding="utf-8")
        assert [row["student_id"] for row in storage.load()] == ["2000001"]


def test_json_storage_appends_and_replaces_rows():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "records.json"
        storage = JSONStorage(path)
        storage.append({"record_id": "a", "student_id": "2600001"})
        storage.append({"record_id": "b", "student_id": "2600002"})
        storage.replace("a", {"record_id": "a", "student_id": "2700001"})
        storage.replace("c", {"record_id": "c", "student_id": "2700003"})
        rows = json.loads(path.read_text(encoding="utf-8"))
        assert [row["record_id"] for row in rows] == ["a", "b", "c"]
        assert rows[0]["student_id"] == "2700001"


def test_a_corrupt_file_is_backed_up_and_never_read_as_empty():
    for corrupt in ('[{"student_id": "2603503"', '"just text"'):
        with case(corrupt=corrupt), tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "records.json"
            storage = JSONStorage(path)
            path.write_text(corrupt, encoding="utf-8")
            with capture_logs("data_manager", level="WARNING"):
                try:
                    storage.load()
                except StorageError as error:
                    assert "Corrupted" in str(error)
                else:
                    raise AssertionError("corrupt data must raise")
            with capture_logs("data_manager", level="WARNING"):
                storage.append({"student_id": "2000123"})
            backups = sorted(Path(temp).glob("records.corrupt-*.json"))
            assert len(backups) == 2
            assert backups[0].read_text(encoding="utf-8") == corrupt
            assert len(storage.load()) == 1


def test_nothing_is_overwritten_when_the_backup_fails():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "records.json"
        storage = JSONStorage(path)
        path.write_text("{broken", encoding="utf-8")
        with patch("storage.json_storage.shutil.copy2",
                   side_effect=OSError("disk full")), \
                capture_logs("data_manager", level="WARNING"):
            try:
                storage.append({"student_id": "2000123"})
            except StorageError as error:
                assert "Backup failed" in str(error)
            else:
                raise AssertionError("a failed backup must raise")
        assert path.read_text(encoding="utf-8") == "{broken"


def test_a_failed_write_reports_an_error_and_leaves_no_temp_file():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "records.json"
        storage = JSONStorage(path)
        try:
            with patch("storage.json_storage.os.replace",
                       side_effect=OSError("read-only")):
                storage.append({"student_id": "2600001"})
        except StorageError as error:
            assert "Could not write" in str(error)
        else:
            raise AssertionError("a failed write must raise")
        assert list(Path(temp).iterdir()) == []


def load_tests(loader, tests, pattern):
    """Let `python -m unittest discover -s tests` run this file's tests."""
    return function_suite(globals())


if __name__ == "__main__":
    unittest.main()
