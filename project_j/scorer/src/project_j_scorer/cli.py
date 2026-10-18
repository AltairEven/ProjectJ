"""project-j-scorer: download the pinned model, or run the resident scoring process."""

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="project-j-scorer",
        description="本机英语发音评分（ADR-0015）：下载模型或运行评分进程。",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("download", help="按固定修订下载模型并核对摘要（唯一联网步骤）")
    fetch.add_argument("--models", type=Path, required=True)
    fetch.add_argument("--cache-dir", type=Path)
    serve = commands.add_parser("serve", help="常驻评分进程：标准输入输出每行一个JSON，不联网")
    serve.add_argument("--models", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "serve":
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from project_j_scorer.serve import protocol_stdout, run

        protocol = protocol_stdout()  # Before importing torch, so its prints go to stderr.
        from project_j_scorer.model import GopEngine

        return run(lambda: GopEngine(args.models), sys.stdin.buffer, protocol)
    if args.command == "download":
        from project_j_scorer.download import download
        from project_j_scorer.model import ModelError

        try:
            manifest = download(args.models, args.cache_dir)
        except ModelError as error:
            print(f"模型核对未通过：{error.kind}（{error.detail}）", file=sys.stderr)
            return 1
        except Exception as error:  # Network or disk failure; kind only.
            print(f"模型下载失败：{type(error).__name__}", file=sys.stderr)
            return 1
        where = f"{manifest['repo']}@{manifest['revision']}"
        print(f"已下载并核对 {where}，共{len(manifest['files'])}个文件。")
        return 0
    return 2
