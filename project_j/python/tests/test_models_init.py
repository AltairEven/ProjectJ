import json
import stat

import pytest

from project_j.platform.models import init
from project_j.platform.models.config import ModelFiles
from project_j.platform.models.registry import load_service


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "project_j").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path / "project_j/private/models"


def cli(*args):
    return init.main(list(args))


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_set_key_is_hidden_private_and_never_printed(root, monkeypatch, capsys):
    monkeypatch.setattr(init.getpass, "getpass", lambda prompt: "sk-secret-123")
    assert cli("set-key", "openai") == 0
    key_file = root / "credentials/openai.json"
    assert mode(key_file) == 0o600 and mode(root) == 0o700 and mode(root / "credentials") == 0o700
    assert "sk-secret-123" not in capsys.readouterr().out
    assert cli("set-key", "simulated") == 1  # no key for the simulated adapter
    assert cli("set-key", "../escape") == 1


def test_configure_validates_adapter_effort_and_purpose(root):
    assert cli("configure", "--default", "nope:m:low") == 1
    assert cli("configure", "--default", "openai:gpt-6.1-sol:turbo") == 1
    assert (
        cli("configure", "--default", "openai:gpt-6.1-sol:low", "--purpose", "chat=openai:x:low")
        == 1
    )
    assert (
        cli(
            "configure",
            "--default",
            "openai:gpt-6.1-sol:low",
            "--purpose",
            "evaluation=openai:gpt-6.1-sol:medium",
        )
        == 0
    )
    bindings = ModelFiles(root).bindings()
    assert bindings["classroom_judgment"].effort == "low"
    assert bindings["evaluation"].effort == "medium"
    assert bindings["planning"].effort == bindings["preparation"].effort == "low"  # the default
    assert (
        cli(
            "configure",
            "--default",
            "openai:gpt-6.1-sol:medium",
            "--purpose",
            "classroom_judgment=openai:gpt-6.1-sol:low",
        )
        == 0
    )
    efforts = {p: b.effort for p, b in ModelFiles(root).bindings().items()}
    assert efforts == {
        "planning": "medium",
        "preparation": "medium",
        "classroom_judgment": "low",
        "evaluation": "medium",
    }
    assert "api_key" not in (root / "config.json").read_text()


def test_probe_records_admission_without_secrets_and_changes_invalidate(root, capsys):
    assert cli("probe") == 1 and "未配置默认模型" in capsys.readouterr().out
    assert cli("configure", "--default", "simulated:simulated-jane:low") == 0
    assert cli("probe") == 0
    record = json.loads((root / "init.json").read_text())
    assert record["initialized"] and mode(root / "init.json") == 0o600
    service = load_service(ModelFiles(root))
    service.require()
    assert cli("status") == 0
    cli("configure", "--default", "simulated:simulated-jane:medium")
    assert "已改动" in load_service(ModelFiles(root)).reasons("evaluation")[0]
    assert cli("status") == 1


def test_key_change_invalidates_and_record_keeps_only_digest(root):
    files = ModelFiles(root)
    files.set_key("openai", "sk-first")
    first = files.digest({"openai": "1"})
    files.set_key("openai", "sk-second")
    assert files.digest({"openai": "1"}) != first
    assert "sk-second" not in first and "sk-second" not in files.digest({"openai": "1"})


def test_symlinked_paths_are_refused(root, tmp_path):
    root.parent.mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    root.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        ModelFiles(root)


def test_init_refuses_outside_project_root(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no project_j here
    assert init.main(["status"]) == 1
    assert "项目根目录" in capsys.readouterr().out
    assert not (tmp_path / "project_j").exists()


def test_corrupt_config_reports_reason(root, capsys):
    root.mkdir(parents=True)
    (root / "config.json").write_text("{not json")
    assert cli("status") == 1
    assert "配置文件无法读取" in capsys.readouterr().out


def test_codex_is_built_with_its_own_home_and_needs_no_key(root, capsys):
    from project_j.platform.models.codex_sdk import CodexAdapter
    from project_j.platform.models.registry import build

    adapter = build("codex", ModelFiles(root))
    assert isinstance(adapter, CodexAdapter) and adapter.home == root / "codex-home"
    assert not (root / "codex-home").exists()  # nothing launched just by building
    assert cli("set-key", "codex") == 1
    assert cli("configure", "--default", "codex:gpt-6.1-sol:low") == 0
    cli("status")
    assert "codex 登录：未登录" in capsys.readouterr().out


def test_login_runs_only_for_codex(root, monkeypatch, capsys):
    from project_j.platform.models.codex_sdk import CodexAdapter

    seen = {}

    async def login(self, show):
        seen["home"] = self.home
        show("https://auth.example/login")
        return True

    async def aclose(self):
        seen["closed"] = True

    monkeypatch.setattr(CodexAdapter, "login", login)
    monkeypatch.setattr(CodexAdapter, "aclose", aclose)
    assert cli("login", "codex") == 0
    assert seen == {"home": root / "codex-home", "closed": True}
    assert "https://auth.example/login" in capsys.readouterr().out
    assert cli("login", "openai") == 1


def test_probe_closes_each_adapter(root, monkeypatch):
    from project_j.platform.models.simulated import SimulatedAdapter

    closed = []

    async def aclose(self):
        closed.append(self.adapter_id)

    monkeypatch.setattr(SimulatedAdapter, "aclose", aclose, raising=False)
    cli("configure", "--default", "simulated:simulated-jane:low")
    assert cli("probe") == 0 and closed == ["simulated"]
