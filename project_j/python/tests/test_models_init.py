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


def test_configure_validates_adapter_effort_and_purpose(root):
    assert cli("configure", "--default", "nope:m:low") == 1
    assert cli("configure", "--default", "simulated:simulated-jane:turbo") == 1
    assert (
        cli(
            "configure",
            "--default",
            "simulated:simulated-jane:low",
            "--purpose",
            "chat=simulated:x:low",
        )
        == 1
    )
    assert (
        cli(
            "configure",
            "--default",
            "simulated:simulated-jane:low",
            "--purpose",
            "evaluation=simulated:simulated-jane:medium",
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
            "simulated:simulated-jane:medium",
            "--purpose",
            "classroom_judgment=simulated:simulated-jane:low",
        )
        == 0
    )
    efforts = {p: b.effort for p, b in ModelFiles(root).bindings().items()}
    assert efforts == {
        "planning": "medium",
        "preparation": "medium",
        "classroom_judgment": "low",
        "evaluation": "medium",
        "vocabulary": "low",  # follows the classroom binding
        "reading": "medium",  # no binding of its own: the default
    }
    assert "api_key" not in (root / "config.json").read_text()


def test_configure_accepts_a_fast_tier_only_where_the_adapter_offers_it(root):
    assert cli("configure", "--default", "simulated:simulated-jane:low:fast") == 1
    assert cli("configure", "--default", "codex:gpt-6.1-sol:low:turbo") == 1
    assert cli("configure", "--default", "codex:gpt-6.1-sol:low:fast:x") == 1
    assert (
        cli(
            "configure",
            "--default",
            "codex:gpt-6.1-sol:medium",
            "--purpose",
            "classroom_judgment=codex:gpt-6.1-sol:low:fast",
        )
        == 0
    )
    tiers = {p: b.tier for p, b in ModelFiles(root).bindings().items()}
    assert tiers == {
        "planning": "default",
        "preparation": "default",
        "classroom_judgment": "fast",
        "evaluation": "default",
        "vocabulary": "fast",
        "reading": "default",
    }


def test_extra_high_effort_is_offered_by_codex_only(root):
    assert cli("configure", "--default", "simulated:simulated-jane:xhigh") == 1
    assert (
        cli(
            "configure",
            "--default",
            "codex:gpt-6.1-sol:xhigh",
            "--purpose",
            "classroom_judgment=codex:gpt-6.1-sol:low:fast",
        )
        == 0
    )
    efforts = {p: b.effort for p, b in ModelFiles(root).bindings().items()}
    assert efforts == {
        "planning": "xhigh",
        "preparation": "xhigh",
        "classroom_judgment": "low",
        "evaluation": "xhigh",
        "vocabulary": "low",
        "reading": "xhigh",
    }


def test_probe_asks_each_adapter_for_the_tiers_its_bindings_use(root, monkeypatch, capsys):
    from project_j.platform.models.simulated import SimulatedAdapter

    seen = []
    original = SimulatedAdapter.probe

    async def probe(self, model, efforts, tiers=()):
        seen.append(tiers)
        return await original(self, model, efforts, tiers)

    monkeypatch.setattr(SimulatedAdapter, "probe", probe)
    monkeypatch.setattr(SimulatedAdapter, "tiers", ("default", "fast"))
    cli(
        "configure",
        "--default",
        "simulated:simulated-jane:medium",
        "--purpose",
        "classroom_judgment=simulated:simulated-jane:low:fast",
    )
    assert cli("probe") == 1  # the simulated report has no tier item
    assert seen == [("fast",)]
    out = capsys.readouterr().out
    assert "simulated:simulated-jane:low:fast" in out and "Fast tier: not probed" in out


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
    assert "changed" in load_service(ModelFiles(root)).reasons("evaluation")[0]
    assert cli("status") == 1


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
    assert "cannot be read" in capsys.readouterr().out


def test_codex_is_built_with_its_own_home_and_needs_no_key(root, capsys):
    from project_j.platform.models.codex_sdk import CodexAdapter
    from project_j.platform.models.registry import build

    adapter = build("codex", ModelFiles(root))
    assert isinstance(adapter, CodexAdapter) and adapter.home == root / "codex-home"
    assert not (root / "codex-home").exists()  # nothing launched just by building
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
    assert cli("login", "simulated") == 1


def test_probe_closes_each_adapter(root, monkeypatch):
    from project_j.platform.models.simulated import SimulatedAdapter

    closed = []

    async def aclose(self):
        closed.append(self.adapter_id)

    monkeypatch.setattr(SimulatedAdapter, "aclose", aclose, raising=False)
    cli("configure", "--default", "simulated:simulated-jane:low")
    assert cli("probe") == 0 and closed == ["simulated"]


def test_digest_of_an_existing_record_stays_valid(root):
    """The digest covers every purpose's binding. Adding the vocabulary purpose (2026-10-24)
    and the reading purpose (2026-11-20) changed it on purpose: an existing study record must
    be probed again (Save and probe) before the new purpose is admitted; removing the API-key
    adapter did not change it."""
    from project_j.platform.models.contracts import Binding

    files = ModelFiles(root)
    files.configure(
        Binding(adapter="codex", model="gpt-6.1-sol", effort="xhigh"),
        {
            "classroom_judgment": Binding(
                adapter="codex", model="gpt-6.1-sol", effort="low", tier="fast"
            )
        },
    )
    assert files.digest({"codex": "3"}) == (
        "4055b8f2903771e809aed3c78939454822e6c9d24d3eafa778fa7ee41f91ceec"
    )


def test_probe_bindings_writes_nothing(root):
    import asyncio

    from project_j.platform.models.contracts import PURPOSES, Binding

    files = ModelFiles(root)
    medium = Binding(adapter="simulated", model="simulated-jane", effort="medium")
    record = asyncio.run(init.probe_bindings(files, {p: medium for p in PURPOSES}))
    assert record["initialized"] and "config_digest" not in record
    assert files.init_record() is None and files.bindings() is None
