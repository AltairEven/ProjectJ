"""Delete every learning record of one study run; development only (ADR-0018)."""

import argparse
import logging
import shutil
from pathlib import Path

from project_j.platform.storage import check_run_id

log = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[5]
STUDY = Path("project_j/private/study")


def reset_study(project_root: Path, run_id: str) -> int:
    """Remove the run's directory (database and resource files); returns the file count."""
    check_run_id(run_id)
    run = project_root / STUDY / run_id
    if run.is_symlink() or not run.is_dir():
        raise FileNotFoundError(f"没有这个正式学习运行编号：{run_id}")
    files = sum(1 for item in run.rglob("*") if item.is_file())
    shutil.rmtree(run)
    log.info("reset run=%s files=%d", run_id, files)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="清空一个正式学习运行编号的全部学习记录（开发期间使用，不可撤销）。"
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    print(
        f"将删除 {args.root / STUDY / args.run_id} 下的全部学习记录与资源文件；服务运行时不要清空。"
    )
    if input("输入运行编号确认：").strip() != args.run_id:
        print("运行编号不符，未清空。")
        return 1
    try:
        removed = reset_study(args.root, args.run_id)
    except (FileNotFoundError, ValueError) as error:
        print(error)
        return 1
    print(f"已清空，删除 {removed} 个文件。下次打开会重新建档。")
    return 0
