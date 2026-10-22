from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, select

from project_j.contracts import SubmittedInput


def test_fastapi_pydantic_contract_exports_without_starting_server():
    app = FastAPI()

    @app.post("/contract-only")
    def unused(body: SubmittedInput):
        return body

    schema = app.openapi()
    assert schema["components"]["schemas"]["SubmittedInput"]["additionalProperties"] is False


def test_sqlalchemy_sqlite_transaction_rollback_in_explicit_temporary_directory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mock.sqlite3'}")
    metadata = MetaData()
    records = Table("compatibility_only", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)
    with pytest.raises(RuntimeError), engine.begin() as connection:
        connection.execute(records.insert().values(id=1))
        raise RuntimeError("injected rollback")
    with engine.connect() as connection:
        assert connection.execute(select(records)).all() == []
    engine.dispose()


def test_explicit_temporary_paths_reject_traversal_and_symlink_escape(tmp_path):
    from project_j.paths import check_path

    assert check_path(tmp_path, "simulation/mock.sqlite3").is_relative_to(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        check_path(tmp_path, "../family.sqlite3")
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        check_path(tmp_path, "escape/file")


def test_offline_preflight_lists_adapters_without_private_config_or_network():
    from project_j.probe import preflight

    result = preflight()
    assert result["mode"] == "offline_preflight" and result["actual_api"] == "unverified"
    assert {item["adapter"] for item in result["adapters"]} == {"simulated", "codex"}
    assert result["live_probe"] == "project-j-init probe"
