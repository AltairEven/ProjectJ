"""Move one study run's learning records to another computer; run with the service stopped.

The archive is the verified backup (database, files and their SHA-256 manifest); the import
restores it into a run that does not exist yet. Models and the Codex login are not included.
"""

import argparse
import json
import logging
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from uuid import uuid4

from project_j.platform.files import PrivateFiles
from project_j.platform.reset import PROJECT_ROOT, STUDY
from project_j.platform.storage import SimulationStore, check_run_id, serving

log = logging.getLogger(__name__)
FOLDER = "project-j-study"  # The archive's one top-level folder.


def export_run(project_root: Path, run_id: str, archive: Path) -> int:
    """Write the run as a private archive; returns the number of files besides the database."""
    check_run_id(run_id)
    run = project_root / STUDY / run_id
    if run.is_symlink() or not (run / "project_j.sqlite3").is_file():
        raise FileNotFoundError(f"没有这个正式学习运行编号：{run_id}")
    if archive.exists():
        raise FileExistsError(f"{archive} 已存在，不覆盖。")
    if serving(run):
        raise RuntimeError("服务正在使用这份学习记录：先在终端按 Ctrl+C 停止服务再导出。")
    store = SimulationStore(project_root / STUDY, run_id, realm="study")
    try:
        backup = PrivateFiles(store).backup()
    finally:
        store.close()
    try:
        files = len(json.loads((backup / "manifest.json").read_text())["files"])
        fd = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as tar:
                tar.add(backup, arcname=FOLDER)
        except BaseException:
            archive.unlink(missing_ok=True)
            raise
    finally:
        shutil.rmtree(backup)
        backups = backup.parent
        if not any(backups.iterdir()):
            backups.rmdir()  # Left as it was when the run had no backups.
    log.info("transfer export run=%s files=%d bytes=%d", run_id, files, archive.stat().st_size)
    return files


def import_run(project_root: Path, run_id: str, archive: Path) -> int:
    """Restore the archive into a new staging run and, all checks passing, name it run_id.
    A failed import never touches run_id; its incomplete staging run is kept, not deleted."""
    check_run_id(run_id)
    root = project_root / STUDY
    target = root / run_id
    if target.exists() or target.is_symlink():
        raise FileExistsError(
            f"这台电脑上已有运行编号 {run_id}，不覆盖：换一个运行编号，"
            "或在服务停止时先用 project-j-reset 清空它。"
        )
    staging = root / f"import-{uuid4().hex}"
    with tempfile.TemporaryDirectory() as temporary:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(temporary, filter="data")
        backup = Path(temporary) / FOLDER
        try:
            PrivateFiles.restore(backup, root, staging.name).close()
        except (OSError, ValueError) as error:
            kept = f"；不完整的导入保留在 {staging}" if staging.exists() else ""
            raise ValueError(
                f"导入未通过核对（{error}），运行编号 {run_id} 未改动{kept}。"
            ) from error
        files = len(json.loads((backup / "manifest.json").read_text())["files"])
    staging.rename(target)
    log.info("transfer import run=%s files=%d", run_id, files)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="导出或导入一个正式学习运行编号的学习记录（换电脑用）；在服务停止时运行。"
    )
    parser.add_argument("action", choices=("export", "import"))
    parser.add_argument("archive", type=Path, help="导出写到的文件，或要导入的文件（.tar.gz）")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        if args.action == "export":
            files = export_run(args.root, args.run_id, args.archive)
            print(
                f"已导出到 {args.archive}（数据库和{files}个素材文件）。"
                "文件里是你的学习记录：自己保存，不要放进仓库或上传共享。"
            )
        else:
            files = import_run(args.root, args.run_id, args.archive)
            print(f"已导入为运行编号 {args.run_id}（数据库和{files}个素材文件，均已核对）。")
    except (EOFError, OSError, RuntimeError, ValueError, tarfile.TarError) as error:
        log.warning("transfer %s refused %s", args.action, type(error).__name__)
        print(error)
        return 1
    return 0
