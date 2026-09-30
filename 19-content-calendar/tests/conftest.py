import pytest

KEY = "test-key"
APPROVER = "approver-test-key"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "calendar.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    # APPROVER_KEY unset now refuses approving (503), so the tests run with it set, like the stack.
    monkeypatch.setenv("APPROVER_KEY", APPROVER)
