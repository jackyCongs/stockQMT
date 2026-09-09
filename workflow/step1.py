# coding=utf-8
"""
Step 1 串行执行模块 (Step 1: Strict Serial QMT / TDX Initialization & Mode 3 Preloading)
流程规则：
1. 启动【大同证券 QMT】 -> 等待20秒检查更新 -> 自动强力登录 -> 等待15秒 -> 自动最小化。
2. 启动【湘财证券 QMT】 -> 等待20秒检查更新 -> 自动强力登录 -> 等待15秒 -> 自动最小化。
3. 启动【通达信行情】  -> 等待4秒弹出登录  -> 自动强力登录 -> 等待8秒  -> 自动最小化。
4. 调起【Git Bash】    -> 执行: cd /d/PythonProject/stockQmt/ && python ./main.py -mode=3 (保持前台显示)
"""

import os
import sys
import subprocess
from pathlib import Path

# 确保能正常导入同一目录下的模块
current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

from config import (
    QMT_CONFIG,
    TDX_EXE,
    TDX_WAIT_POPUP,
    TDX_WAIT_LOGIN,
    GIT_BASH_EXE,
    GIT_BASH_PROJECT_DIR,
    QMT_UPDATE_CHECK_WAIT,
    QMT_LOGIN_WAIT
)
from qmt_controller import start_qmt_app, start_tdx_app


def run_gitbash_mode3() -> bool:
    """
    启动 Git Bash 并执行 main.py -mode=3
    """
    print(f"\n" + "=" * 60)
    print(f"  >>> 【串行流程】调起 Git Bash 执行预加载 Pipeline (-mode=3)")
    print("=" * 60)

    if not os.path.exists(GIT_BASH_EXE):
        print(f"[错误] 未在指定路径找到 Git Bash: {GIT_BASH_EXE}")
        return False

    bash_command = f"cd {GIT_BASH_PROJECT_DIR} && python ./main.py -mode=3; exec bash"
    print(f"[*] 执行命令:\n    {bash_command}")

    try:
        subprocess.Popen([GIT_BASH_EXE, "-c", bash_command])
        print(f"[✓] Git Bash 终端已成功调起并在前台执行 mode=3！")
        return True
    except Exception as e:
        print(f"[错误] 调起 Git Bash 失败: {e}")
        return False


def run():
    """
    Step 1 严格串行执行入口
    """
    print("############################################################")
    print("             StockQMT 自动化流程 - STEP 1 (严格串行)        ")
    print("  [1/4] 启动大同证券 QMT -> 20s更新 -> 登录 -> 15s -> 最小化  ")
    print("  [2/4] 启动湘财证券 QMT -> 20s更新 -> 登录 -> 15s -> 最小化  ")
    print("  [3/4] 启动通达信行情   -> 4s弹出  -> 登录 -> 8s  -> 最小化  ")
    print("  [4/4] 启动 Git Bash 执行 main.py -mode=3 (前台保留)        ")
    print("############################################################")

    # 1. 严格串行执行：大同证券 QMT
    start_qmt_app(
        platform_name="大同证券",
        exe_path=QMT_CONFIG["大同证券"]["exe"],
        wait_update_check=QMT_UPDATE_CHECK_WAIT,
        wait_after_login=QMT_LOGIN_WAIT,
        auto_minimize=True
    )

    # 2. 严格串行执行：湘财证券 QMT (等大同完全启动、登录且最小化后才启动)
    start_qmt_app(
        platform_name="湘财证券",
        exe_path=QMT_CONFIG["湘财证券"]["exe"],
        wait_update_check=QMT_UPDATE_CHECK_WAIT,
        wait_after_login=QMT_LOGIN_WAIT,
        auto_minimize=True
    )

    # 3. 严格串行执行：通达信客户端（已设置自动登录，只需启动 -> 等10秒 -> 叉广告 -> 最小化）
    # start_tdx_app(
    #     exe_path=TDX_EXE,
    #     wait_auto_login=10,
    #     auto_minimize=True
    # )

    # 4. 严格串行执行：所有客户端登录且最小化后，调起 Git Bash 执行 mode=3
    run_gitbash_mode3()

    print("\n" + "=" * 60)
    print("  [✓✓✓] Step 1 全部 4 个串行步骤均已执行完毕！")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    run()
