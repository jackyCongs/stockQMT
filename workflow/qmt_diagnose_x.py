# coding=utf-8
import sys
import time
import win32gui
import pyautogui

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

print("==================================================")
print("      QMT X轴(水平方向)盲测工具")
print("==================================================")

qmt_windows = []
def enum_cb(hwnd, _):
    if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
        title = win32gui.GetWindowText(hwnd)
        rect = win32gui.GetWindowRect(hwnd)
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        if "极速策略" in title or "交易" in title or "迅投" in title or "XtItClient" in title:
            if 300 <= w <= 1000 and 200 <= h <= 800:
                qmt_windows.append((hwnd, title, rect, w, h))

win32gui.EnumWindows(enum_cb, None)
if not qmt_windows:
    print("未找到 QMT 登录窗口")
    sys.exit(0)

hwnd, title, rect, w, h = qmt_windows[0]
print(f"锁定窗口: '{title}' (尺寸: {w}x{h})")

left, top, right, bottom = rect

# 高度已经确定是 79%
cy = top + int(h * 0.79)

try:
    win32gui.ShowWindow(hwnd, 9)
    win32gui.SetForegroundWindow(hwnd)
except Exception:
    pass
time.sleep(1)

# X轴比例测试
x_ratios = [0.25, 0.30, 0.35, 0.40, 0.45]

for idx, ratio in enumerate(x_ratios):
    cx = left + int(w * ratio)
    print(f"\n[{idx+1}/5] 测试 X 比例: {ratio*100:.0f}% (窗口宽度: {w} -> X偏移: {int(w*ratio)})")
    pyautogui.moveTo(cx, cy, duration=0.3)
    print(">>> 观察鼠标位置...等待 3 秒")
    time.sleep(3)

print("\n==================================================")
print("测试完毕！请告诉我 1、2、3、4、5 哪一次落在【登录】按钮中心？")
