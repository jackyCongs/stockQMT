# coding=utf-8
"""
工作流统一配置文件 (Workflow Configuration)
集中管理 QMT、通达信客户端路径、Git Bash 路径以及项目基础配置
"""

import os
from pathlib import Path

# 项目根目录 (d:\PythonProject\stockQMT)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Git Bash 格式的项目路径 (如 /d/PythonProject/stockQmt/)
drive_letter = PROJECT_ROOT.drive[0].lower()
rel_path = PROJECT_ROOT.as_posix()[3:]
GIT_BASH_PROJECT_DIR = f"/{drive_letter}/{rel_path}"

# 常用工具路径
GIT_BASH_EXE = r"D:\GitBash\Git\git-bash.exe"

# 通达信配置
TDX_EXE = r"D:\stock_software\tongdaxin\TdxW.exe"
TDX_WAIT_POPUP = 4           # 启动通达信后等待登录界面弹出的时间 (秒)
TDX_WAIT_LOGIN = 8           # 点击登录后等待通达信连接行情服务器的时间 (秒)

# QMT 客户端启动时间配置（秒）
QMT_UPDATE_CHECK_WAIT = 20    # 启动客户端后等待检查更新及加载登录窗口的时间 (默认20秒)
QMT_LOGIN_WAIT = 10           # 点击登录按钮后等待客户端加载数据及建立交易通道的时间 (默认10秒)

# QMT 客户端路径映射表
QMT_CONFIG = {
    "大同证券": {
        "exe": r"D:\QMT\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64\XtItClient.exe",
        "mini_data": r"D:\QMT\迅投极速策略交易系统交易终端 大同证券QMT实盘\userdata_mini",
    },
    "湘财证券": {
        "exe": r"D:\QMT\湘财迅投QMT极速策略交易系统（交易端）\bin.x64\XtItClient.exe",
        "mini_data": r"D:\QMT\湘财迅投QMT极速策略交易系统（交易端）\userdata_mini",
    },
}
