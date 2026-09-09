# coding=utf-8
import sys
import time
import win32gui
import pyautogui

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

print("==================================================")
print("      QMT 几何坐标盲测工具")
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
cx = left + (w // 2)

# 尝试把窗口放前台 (忽略报错)
try:
    win32gui.ShowWindow(hwnd, 9)
    win32gui.SetForegroundWindow(hwnd)
except Exception:
    pass

time.sleep(1)

test_ratios = [0.73, 0.76, 0.79, 0.82, 0.85]

for idx, ratio in enumerate(test_ratios):
    cy = top + int(h * ratio)
    print(f"\n[{idx+1}/5] 正在测试高度比例: {ratio*100:.0f}% (窗口高度: {h} -> Y偏移: {int(h*ratio)})")
    pyautogui.moveTo(cx, cy, duration=0.3)
    print(">>> 观察鼠标位置...等待 3 秒")
    time.sleep(3)

print("\n==================================================")
print("测试完毕！请告诉我 1、2、3、4、5 这五次里，哪一次鼠标刚好落在【登录】按钮中心？")
