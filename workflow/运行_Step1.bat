@echo off
chcp 65001 >nul
cd /d "%~dp0"
title StockQMT 自动化流程 - Step 1
echo ========================================================
echo   StockQMT 自动化工作流 - Step 1 (严格串行)
echo   1. 启动大同证券 QMT 并登录 (自动最小化)
echo   2. 启动湘财证券 QMT 并登录 (自动最小化)
echo   3. 启动通达信 TdxW 并登录 (自动最小化)
echo   4. 启动 Git Bash 执行 main.py -mode=3
echo ========================================================
python step1.py
pause
