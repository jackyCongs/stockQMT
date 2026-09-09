# coding=utf-8
"""
通达信登录按钮定位诊断工具
在通达信登录界面打开的状态下运行此脚本：
1. 检测 DPI 缩放
2. 枚举所有通达信窗口及其精确 Rect
3. 枚举子控件并打印每个控件的文字和屏幕坐标
4. 截图并保存当前屏幕
5. 将鼠标移到计算的"登录按钮"位置（不点击），供目视确认
"""
import sys
import os
import time
import ctypes

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

user32 = ctypes.windll.user32

# 检测 DPI
print("=" * 60)
print("  通达信登录按钮定位诊断工具")
print("=" * 60)

# 1. DPI 信息
print("\n[1] DPI 信息:")
try:
    # 不设置 DPI awareness，看默认值
    dpi = user32.GetDpiForSystem()
    print(f"    系统 DPI: {dpi} (缩放比: {dpi / 96.0 * 100:.0f}%)")
except Exception:
    print("    无法获取系统 DPI (可能是 Win7)")

screen_w_raw = user32.GetSystemMetrics(0)
screen_h_raw = user32.GetSystemMetrics(1)
print(f"    屏幕分辨率 (未设DPI感知): {screen_w_raw}x{screen_h_raw}")

# 现在设置 DPI 感知
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    print("    已启用 Per-Monitor DPI 感知")
except Exception:
    try:
        user32.SetProcessDPIAware()
        print("    已启用系统级 DPI 感知")
    except Exception:
        print("    DPI 感知设置失败")

screen_w_dpi = user32.GetSystemMetrics(0)
screen_h_dpi = user32.GetSystemMetrics(1)
print(f"    屏幕分辨率 (设DPI感知后): {screen_w_dpi}x{screen_h_dpi}")

if screen_w_raw != screen_w_dpi:
    print(f"    ⚠️  检测到 DPI 缩放！未感知: {screen_w_raw}x{screen_h_raw}, 物理: {screen_w_dpi}x{screen_h_dpi}")
    print(f"    ⚠️  缩放因子: {screen_w_dpi / screen_w_raw:.2f}x")
else:
    print(f"    ✓ 无 DPI 缩放差异")


# 2. 枚举所有通达信相关窗口
print("\n[2] 通达信相关窗口:")
import win32gui
import win32process
import psutil

tdx_pids = set()
for proc in psutil.process_iter(['name', 'pid']):
    if 'tdx' in (proc.info.get('name') or '').lower():
        tdx_pids.add(proc.info['pid'])
        print(f"    找到通达信进程: PID={proc.info['pid']}, Name={proc.info['name']}")

if not tdx_pids:
    print("    ⚠️  未找到通达信进程！请先启动通达信再运行此脚本。")
    sys.exit(1)

all_windows = []
def enum_cb(hwnd, _):
    if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        title = win32gui.GetWindowText(hwnd)
        rect = win32gui.GetWindowRect(hwnd)
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        if pid in tdx_pids and w > 50 and h > 50:
            all_windows.append((hwnd, pid, title, rect, w, h))

win32gui.EnumWindows(enum_cb, None)

login_dialog = None
for hwnd, pid, title, rect, w, h in all_windows:
    marker = ""
    if 420 <= w <= 800 and 320 <= h <= 650:
        marker = " ← 疑似登录对话框"
        login_dialog = (hwnd, pid, title, rect, w, h)
    print(f"    句柄: {hwnd}, PID: {pid}, 标题: '{title}', Rect: {rect}, 尺寸: {w}x{h}{marker}")


# 3. 枚举登录对话框的所有子控件
if login_dialog:
    hwnd, pid, title, rect, w, h = login_dialog
    print(f"\n[3] 登录对话框子控件 (句柄: {hwnd}, 尺寸: {w}x{h}):")
    
    child_controls = []
    def enum_child_cb(chwnd, _):
        if win32gui.IsWindow(chwnd):
            ctitle = win32gui.GetWindowText(chwnd)
            cclass = win32gui.GetClassName(chwnd)
            crect = win32gui.GetWindowRect(chwnd)
            cw = crect[2] - crect[0]
            ch = crect[3] - crect[1]
            visible = win32gui.IsWindowVisible(chwnd)
            if cw > 0 and ch > 0:
                child_controls.append((chwnd, ctitle, cclass, crect, cw, ch, visible))
    
    win32gui.EnumChildWindows(hwnd, enum_child_cb, None)
    
    for chwnd, ctitle, cclass, crect, cw, ch, visible in child_controls:
        vis_str = "可见" if visible else "隐藏"
        cx = (crect[0] + crect[2]) // 2
        cy = (crect[1] + crect[3]) // 2
        # 相对于父窗口的偏移
        rel_x = cx - rect[0]
        rel_y = cy - rect[1]
        marker = ""
        if "登录" in ctitle or "登 录" in ctitle:
            marker = " ★★★ 登录按钮 ★★★"
        print(f"    [{vis_str}] 句柄:{chwnd} 类:{cclass} 文字:'{ctitle}' Rect:{crect} 中心:({cx},{cy}) 相对:({rel_x},{rel_y}) 尺寸:{cw}x{ch}{marker}")

    # 4. 计算预期点击位置并移动鼠标
    print(f"\n[4] 鼠标定位测试 (只移动，不点击):")
    
    left, top, right, bottom = rect
    
    # 方案 A: 按比例 (196/564, 369/428)
    pos_a_x = left + int(w * (196.0 / 564.0))
    pos_a_y = top + int(h * (369.0 / 428.0))
    print(f"    方案 A (比例 196/564, 369/428): ({pos_a_x}, {pos_a_y})")
    
    # 方案 B: 底部 -60
    pos_b_x = left + int(w * 0.347)
    pos_b_y = bottom - 60
    print(f"    方案 B (底部 -60): ({pos_b_x}, {pos_b_y})")
    
    # 方案 C: 找到实际的"登录"子控件位置
    login_btns = [(chwnd, ctitle, crect) for chwnd, ctitle, cclass, crect, cw, ch, vis in child_controls 
                  if ("登录" in ctitle or "登 录" in ctitle) and vis and cw > 20]
    if login_btns:
        for chwnd, ctitle, crect in login_btns:
            cx = (crect[0] + crect[2]) // 2
            cy = (crect[1] + crect[3]) // 2
            print(f"    方案 C (子控件 '{ctitle}'): ({cx}, {cy})  ← 控件实际 Rect: {crect}")
    
    # 激活窗口
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
    win32gui.BringWindowToTop(hwnd)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    
    # 移动鼠标到方案 A 位置（不点击）
    import pyautogui
    print(f"\n    >>> 正在将鼠标移到方案 A 位置 ({pos_a_x}, {pos_a_y})...")
    print(f"    >>> 请观察鼠标是否在红底【登 录】按钮上！")
    print(f"    >>> 3 秒后移动...")
    time.sleep(3)
    pyautogui.moveTo(pos_a_x, pos_a_y, duration=0.3)
    print(f"    >>> 鼠标已移动到方案 A！请目视确认位置。等待 5 秒...")
    time.sleep(5)
    
    # 移动到方案 B
    print(f"\n    >>> 正在将鼠标移到方案 B 位置 ({pos_b_x}, {pos_b_y})...")
    pyautogui.moveTo(pos_b_x, pos_b_y, duration=0.3)
    print(f"    >>> 鼠标已移动到方案 B！请目视确认位置。等待 5 秒...")
    time.sleep(5)
    
    # 如果找到了子控件登录按钮
    if login_btns:
        chwnd, ctitle, crect = login_btns[0]
        cx = (crect[0] + crect[2]) // 2
        cy = (crect[1] + crect[3]) // 2
        print(f"\n    >>> 正在将鼠标移到方案 C (子控件实际位置) ({cx}, {cy})...")
        pyautogui.moveTo(cx, cy, duration=0.3)
        print(f"    >>> 鼠标已移动到方案 C！请目视确认位置。等待 5 秒...")
        time.sleep(5)

    # 5. 截图保存
    print(f"\n[5] 保存当前屏幕截图...")
    from PIL import ImageGrab
    screenshot = ImageGrab.grab()
    save_path = os.path.join(os.path.dirname(__file__), "诊断截图.png")
    screenshot.save(save_path)
    print(f"    截图已保存: {save_path}")

else:
    print("\n[3] ⚠️  未找到通达信登录对话框窗口！")

print("\n" + "=" * 60)
print("  诊断完成！请将上面的输出和鼠标位置情况反馈给我。")
print("=" * 60)
