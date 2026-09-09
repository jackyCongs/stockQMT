# coding=utf-8
"""
工作流总调度器 (Workflow Runner)
支持按步骤执行（Step 1, Step 2, ...）或一键执行全部流程
"""

import sys
import argparse
from pathlib import Path

current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

import step1


def main():
    parser = argparse.ArgumentParser(description="StockQMT 自动化工作流总调度器")
    parser.add_argument("--step", type=str, default="1", choices=["1", "all"],
                        help="指定要运行的步骤编号 (默认: 1)")

    args = parser.parse_args()

    if args.step == "1":
        step1.run()
    elif args.step == "all":
        print("[*] 开始执行全流程自动化...")
        step1.run()
        # 后续 Step 2, Step 3 直接在此串联
    else:
        print(f"[提示] 未知步骤: {args.step}")


if __name__ == "__main__":
    main()
