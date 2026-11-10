"""Автосервис не может помечать спонсора снятым."""
from scripts.maintenance_once import service_once


class Cursor:
    def __init__(self, rows=(), rowcount=0):
        self.rows = rows
        self.rowcount = rowcount

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class FakeDB:
    def __init__(self):
        self.sql = []

    def execute(self, sql, args=None):
        self.sql.append((sql, args))
        if "pg_try_advisory" in sql:
            return Cursor([{"acquired": True}])
        if "FROM slot_holds" in sql and "SELECT" in sql:
            return Cursor([{"id": "hold-1"}])
        if "DELETE FROM slot_holds" in sql:
            return Cursor(rowcount=1)
        if "FROM campaigns" in sql:
            return Cursor([{"id": "campaign-1"}])
        raise AssertionError(sql)


def test_dry_run_does_not_change_database():
    db = FakeDB()
    result = service_once(db)
    assert result["expired_holds"] == 1
    assert result["deleted_holds"] == 0
    assert result["overdue_campaign_ids"] == ["campaign-1"]
    assert all("DELETE" not in sql and "UPDATE" not in sql for sql, _ in db.sql)


def test_apply_deletes_only_holds_not_campaigns():
    db = FakeDB()
    result = service_once(db, apply=True)
    assert result["deleted_holds"] == 1
    assert any("DELETE FROM slot_holds" in sql for sql, _ in db.sql)
    assert all("UPDATE campaigns" not in sql and "DELETE FROM campaigns" not in sql
               for sql, _ in db.sql)
    assert result["overdue_requires_manual_verification"] is True


def test_bad_batch_size_rejected():
    import pytest
    for value in (0, -1, 1001, True):
        with pytest.raises(ValueError):
            service_once(FakeDB(), limit=value)
