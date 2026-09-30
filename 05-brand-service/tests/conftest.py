import pytest


@pytest.fixture(autouse=True)
def _isolated_fact_store(tmp_path, monkeypatch):
    """Every test gets its own (not yet created) fact store and no owner key."""
    monkeypatch.setenv("FACTS_DB", str(tmp_path / "facts.sqlite"))
    monkeypatch.delenv("FACT_OWNER_KEY", raising=False)
