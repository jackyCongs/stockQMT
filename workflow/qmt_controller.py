# coding=utf-8
"""
客户端与软件控制器 (App Controller)
负责检测 QMT / 通达信等进程、串行拉起客户端、等待检查更新：
- QMT：UIAutomation 按钮识别 + 实体回车 + 居中 78% 物理坐标点击全通道登录
- 通达信：无留白高精度 OpenCV 视觉模板匹配 + 闭环验证二次登录
- 登录后自动最小化窗口
"""

import os
import sys
import time
import ctypes
import subprocess
from pathlib import Path
import cv2
import numpy as np
from PIL import ImageGrab
import pyautogui
import psutil

# Windows API (不设置 DPI 感知，保持与 pyautogui / UIAutomation 坐标系一致)
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 保证控制台 UTF-8 输出
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# 资源文件目录
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
TDX_RED_LOGIN_BTN_IMG = str(ASSETS_DIR / "tdx_red_login_btn.png")
TDX_CLOSE_AD_BTN_IMG = str(ASSETS_DIR / "tdx_close_ad_btn.png")


def countdown(seconds: int, message: str):
    """
    可视化倒计时
    """
    for remaining in range(seconds, 0, -1):
        print(f"    ⏳ [{message}] 倒计时: {remaining:2d} 秒...", end="\r", flush=True)
        time.sleep(1)
    print(f"    ✓ [{message}] 等待结束！" + " " * 30)


def click_at(x: int, y: int):
    """
    可靠的物理鼠标点击：移动 -> 悬停 -> 物理按下 -> 延时 -> 物理释放
    """
    pyautogui.moveTo(int(x), int(y), duration=0.1)
    time.sleep(0.08)
    pyautogui.mouseDown(int(x), int(y))
    time.sleep(0.15)
    pyautogui.mouseUp(int(x), int(y))
    time.sleep(0.08)


def is_process_running(exe_path: str) -> bool:
    """
    检查指定 exe 路径的进程是否正在运行
    """
    target_name = Path(exe_path).name.lower()
    target_path = os.path.normpath(exe_path).lower()

    try:
        for proc in psutil.process_iter(['name', 'exe']):
            try:
                p_name = (proc.info.get('name') or '').lower()
                if p_name == target_name:
                    p_exe = proc.info.get('exe')
                    if p_exe and os.path.normpath(p_exe).lower() == target_path:
                        return True
                    elif not p_exe:
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return False
    except Exception:
        try:
            res = subprocess.run(f'tasklist /FI "IMAGENAME eq {target_name}"', capture_output=True, text=True, shell=True)
            return target_name in res.stdout.lower()
        except Exception:
            return False


def get_process_family_pids(root_pid: int) -> set:
    """
    获取指定 PID 及其所有子进程的 PID 集合
    """
    pids = {root_pid}
    try:
        parent = psutil.Process(root_pid)
        for child in parent.children(recursive=True):
            pids.add(child.pid)
    except Exception:
        pass
    return pids


def force_focus_window(hwnd: int) -> bool:
    """
    强力将指定窗口激活并置顶到前台（突破 Windows 前台焦点限制）
    """
    try:
        import win32gui
        import win32con

        if not win32gui.IsWindow(hwnd):
            return False

        # 模拟按下释放 Alt 键，解除 Windows 焦点锁
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)

        # 还原并置顶
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        return True
    except Exception:
        return False


def find_image_on_screen(template_path: str, confidence: float = 0.75):
    """
    在全屏幕截图中查找图元物理坐标
    """
    if not os.path.exists(template_path):
        return None

    try:
        screenshot = ImageGrab.grab()
        screen_np = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
        template = cv2.imdecode(np.fromfile(template_path, dtype=np.uint8), cv2.IMREAD_COLOR)

        res = cv2.matchTemplate(screen_np, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

        if max_val >= confidence:
            th, tw, _ = template.shape
            center_x = max_loc[0] + tw // 2
            center_y = max_loc[1] + th // 2
            return center_x, center_y, max_val
    except Exception:
        pass

    return None


def find_login_window(platform_name: str, root_pid: int = None, exclude_keywords: list = None):
    """
    通用查找登录主窗口句柄和位置 (供 QMT 使用)
    """
    if exclude_keywords is None:
        exclude_keywords = ["扫码", "二维码", "QRCode"]

    try:
        import win32gui
        import win32process

        target_pids = get_process_family_pids(root_pid) if root_pid else set()
        matched_windows = []

        def enum_cb(hwnd, _):
            if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                title = win32gui.GetWindowText(hwnd)
                rect = win32gui.GetWindowRect(hwnd)
                width = rect[2] - rect[0]
                height = rect[3] - rect[1]

                if any(ex in title for ex in exclude_keywords):
                    return

                # QMT 登录对话框的尺寸特征（严格限制，防止登录成功后将弹出的主交易界面错认为登录框）
                if 200 <= width <= 800 and 150 <= height <= 600:
                    if pid in target_pids:
                        matched_windows.append((hwnd, pid, title, rect))
                    elif platform_name in title or "交易" in title or "策略" in title or "XtItClient" in title:
                        matched_windows.append((hwnd, pid, title, rect))

        win32gui.EnumWindows(enum_cb, None)

        if matched_windows:
            return matched_windows[0]
        return None
    except Exception:
        return None


def trigger_qmt_login(platform_name: str, proc_pid: int = None, timeout: int = 30) -> bool:
    """
    QMT 全自动闭环登录（最多重试 5 轮，每轮全通道轰炸 + 验证窗口是否消失）：
    1. Win32 EnumChildWindows 找到登录按钮句柄 -> BM_CLICK 直发消息（最可靠）
    2. UIAutomation 识别按钮 -> Click
    3. 硬件回车键
    4. 窗口居中 78% 坐标物理鼠标点击
    5. 验证登录窗口是否已消失 -> 未消失则自动重试
    """
    print(f"[*] 正在定位【{platform_name}】登录窗口并触发登录（闭环验证模式）...")
    valid_btn_keywords = ["登录", "登 录", "确定", "Login"]

    for attempt in range(1, 6):
        win_info = find_login_window(platform_name, proc_pid, exclude_keywords=["扫码", "二维码"])
        if not win_info:
            print(f"[*] 未检测到【{platform_name}】登录窗口（可能已登录成功）")
            return True

        hwnd, pid, title, rect = win_info
        left, top, right, bottom = rect
        w = right - left
        h = bottom - top
        print(f"[*] [第 {attempt}/5 轮] 锁定窗口: '{title}' (句柄: {hwnd}, 区域: {rect})")

        # 1. 强力置顶激活窗口
        force_focus_window(hwnd)
        time.sleep(0.3)

        # 通道 A: Win32 BM_CLICK（最可靠，直接发消息给按钮句柄）
        try:
            import win32gui
            import win32con
            child_btns = []
            def enum_child_cb(child_hwnd, _):
                if win32gui.IsWindow(child_hwnd) and win32gui.IsWindowVisible(child_hwnd):
                    ctitle = win32gui.GetWindowText(child_hwnd).replace(" ", "")
                    if any(k in ctitle for k in valid_btn_keywords):
                        child_btns.append((child_hwnd, ctitle))
            win32gui.EnumChildWindows(hwnd, enum_child_cb, None)
            for btn_hwnd, btn_title in child_btns:
                print(f"[*] [通道A BM_CLICK] 找到按钮 '{btn_title}' (句柄: {btn_hwnd})，发送点击消息！")
                win32gui.SendMessage(btn_hwnd, win32con.BM_CLICK, 0, 0)
                time.sleep(0.1)
        except Exception:
            pass

        # 通道 B: UIAutomation 识别并点击
        try:
            import uiautomation as auto
            win_ctrl = auto.ControlFromHandle(hwnd)
            if win_ctrl.Exists(0, 0):
                for btn in win_ctrl.GetChildren():
                    if btn.ControlType == auto.ControlType.ButtonControl:
                        bname = (btn.Name or "").replace(" ", "")
                        if any(k in bname for k in valid_btn_keywords):
                            print(f"[*] [通道B UIAutomation] 识别到按钮 '{btn.Name}'，执行点击！")
                            btn.Click(simulateMove=False)
                            time.sleep(0.1)
        except Exception:
            pass

        # 通道 C: 硬件回车键
        print(f"[*] [通道C 回车键] 发送 Enter 键...")
        force_focus_window(hwnd)
        time.sleep(0.1)
        user32.keybd_event(0x0D, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(0x0D, 0, 2, 0)

        # 通道 D: 窗口绝对比例物理点击
        # 湘财和大同界面有微小差异，分别定制高度
        click_x = left + int(w * 0.40)
        if "湘财" in platform_name:
            click_y = top + int(h * 0.82)
        else:
            click_y = top + int(h * 0.79)
            
        print(f"[*] [通道D 坐标点击] 在精准实测坐标 ({click_x}, {click_y}) 执行物理点击...")
        click_at(click_x, click_y)

        # 闭环验证：等待 4 秒后检查登录窗口是否已消失
        time.sleep(4.0)
        check = find_login_window(platform_name, proc_pid, exclude_keywords=["扫码", "二维码"])
        if not check:
            print(f"[✓✓] 验证通过：【{platform_name}】登录窗口已消失，登录成功！")
            return True
        else:
            print(f"[*] 登录窗口仍在，准备第 {attempt + 1} 轮重试...")
            time.sleep(1.0)

    print(f"[警告] 【{platform_name}】经过 5 轮尝试仍未成功登录")
    return False


def find_tdx_login_dialog(proc_pid: int = None):
    """
    精准过滤通达信真实登录主对话框（尺寸大约在 420x320 ~ 750x600 之间，过滤 1920x1080 铺底大窗口）
    """
    try:
        import win32gui
        import win32process

        target_pids = get_process_family_pids(proc_pid) if proc_pid else set()
        matched = []

        def enum_cb(hwnd, _):
            if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                title = win32gui.GetWindowText(hwnd)
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]

                if 420 <= w <= 800 and 320 <= h <= 650:
                    if "扫码" not in title and "二维码" not in title:
                        if (target_pids and pid in target_pids) or "通达信" in title or "Tdx" in title:
                            matched.append((hwnd, pid, title, rect, w, h))

        win32gui.EnumWindows(enum_cb, None)

        if matched:
            for item in matched:
                if "通达信" in item[2]:
                    return item
            return matched[0]
        return None
    except Exception:
        return None


def get_tdx_red_button_pos(proc_pid: int = None):
    """
    精准获取通达信红底【登 录】大按钮的绝对物理屏幕坐标 (btn_x, btn_y)
    1. 优先使用无留白精细 OpenCV 模板匹配 (中心直指红按钮正中心)
    2. 几何引擎按实测像素精确对齐 (相对窗口 196px, 369px)
    """
    # 1. 尝试 OpenCV 视觉锁定
    img_match = find_image_on_screen(TDX_RED_LOGIN_BTN_IMG, confidence=0.75)
    if img_match:
        cx, cy, score = img_match
        print(f"[✓] 【OpenCV视觉引擎】在屏幕物理坐标 ({cx}, {cy}) 100% 命中红底【登 录】大按钮！(匹配度: {score:.3f})")
        return cx, cy

    # 2. 几何引擎（严格按实测图像比例: X: 196/564, Y: 369/428）
    dlg_info = find_tdx_login_dialog(proc_pid)
    if dlg_info:
        hwnd, pid, title, rect, w, h = dlg_info
        force_focus_window(hwnd)
        time.sleep(0.2)
        import win32gui
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        btn_x = left + int(w * (196.0 / 564.0))
        btn_y = top + int(h * (369.0 / 428.0))
        print(f"[✓] 【几何引擎】对准红底【登 录】按钮物理坐标: ({btn_x}, {btn_y}) (窗口左上角: {left}, {top})")
        return btn_x, btn_y

    return None, None


def close_tdx_qrcode_window(proc_pid: int = None, timeout: int = 6) -> bool:
    """
    专门检测并仅通过鼠标点击右上角 ✕ 叉掉通达信的『扫码登录』/广告弹窗
    """
    print(f"[*] [通达信广告拦截] 正在扫描广告弹窗并点击右上角 ✕ 叉掉...")
    try:
        import win32gui
        import win32process

        target_pids = get_process_family_pids(proc_pid) if proc_pid else set()
        start_time = time.time()

        while time.time() - start_time < timeout:
            # 1. 优先尝试图像识别右上角 ✕ 叉号
            img_match = find_image_on_screen(TDX_CLOSE_AD_BTN_IMG, confidence=0.85)
            if img_match:
                cx, cy, _ = img_match
                print(f"[✓] 【图像引擎】识别到广告右上角 ✕ 坐标: ({cx}, {cy})，执行点击！")
                click_at(cx, cy)
                time.sleep(0.8)
                return True

            # 2. 通过窗口句柄定位广告窗口并点击右上角
            qrcode_windows = []
            def enum_cb(hwnd, _):
                if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    title = win32gui.GetWindowText(hwnd)
                    rect = win32gui.GetWindowRect(hwnd)
                    width = rect[2] - rect[0]
                    height = rect[3] - rect[1]

                    if width > 150 and height > 150:
                        if (target_pids and pid in target_pids) or "通达信" in title or "扫码" in title:
                            if "扫码" in title or "二维码" in title or "今日要闻" in title or "资讯" in title or "广告" in title:
                                qrcode_windows.append((hwnd, pid, title, rect))

            win32gui.EnumWindows(enum_cb, None)

            if qrcode_windows:
                hwnd, pid, title, rect = qrcode_windows[0]
                left, top, right, bottom = rect
                print(f"[✓] 定位到广告弹窗: '{title}' (句柄: {hwnd}, 区域: {rect})")

                force_focus_window(hwnd)
                time.sleep(0.2)

                close_btn_x = right - 25
                close_btn_y = top + 25
                print(f"[*] 鼠标物理点击右上角 ✕ 叉号坐标 ({close_btn_x}, {close_btn_y})...")
                click_at(close_btn_x, close_btn_y)
                time.sleep(0.8)
                return True

            time.sleep(0.4)

        print("[提示] 未检测到广告弹窗（可能未弹出或已自动关闭）。")
        return False
    except Exception as e:
        print(f"[警告] 叉掉广告弹窗时发生异常: {e}")
        return False


def minimize_qmt_windows(platform_name: str, root_pid: int = None):
    """
    将已登录完成的客户端主窗口最小化
    """
    print(f"[*] 正在将【{platform_name}】窗口最小化...")
    try:
        import win32gui
        import win32con
        import win32process

        target_pids = get_process_family_pids(root_pid) if root_pid else set()
        minimized_count = 0

        def enum_cb(hwnd, _):
            nonlocal minimized_count
            if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                title = win32gui.GetWindowText(hwnd)
                rect = win32gui.GetWindowRect(hwnd)
                width = rect[2] - rect[0]
                height = rect[3] - rect[1]

                if width > 100 and height > 100:
                    is_match = False
                    if target_pids and pid in target_pids:
                        is_match = True
                    elif any(kw in title for kw in [platform_name, "极速策略", "XtItClient", "大同证券", "湘财证券", "通达信", "TdxW", "QMT"]):
                        is_match = True

                    if is_match:
                        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                        minimized_count += 1

        win32gui.EnumWindows(enum_cb, None)
        if minimized_count > 0:
            print(f"[✓] 已成功最小化【{platform_name}】窗口（共处理 {minimized_count} 个相关窗口）。")
        else:
            print(f"[提示] 未找到需要最小化的【{platform_name}】可见窗口（可能已处于托盘或后台）。")
    except Exception as e:
        print(f"[警告] 最小化【{platform_name}】窗口时出错: {e}")


def start_qmt_app(
    platform_name: str,
    exe_path: str,
    wait_update_check: int = 20,
    wait_after_login: int = 15,
    auto_minimize: bool = True
) -> bool:
    """
    严格串行启动指定券商平台的 QMT 客户端：
    1. 拉起进程
    2. 等待检查更新与登录窗口完全就绪 (wait_update_check 秒，默认 20s)
    3. 精确触发登录
    4. 等待完成登录并进入主界面 (wait_after_login 秒，默认 15s)
    5. 登录成功后自动将 QMT 客户端最小化 (auto_minimize=True)
    """
    print(f"\n" + "=" * 60)
    print(f"  >>> 【串行流程】正在启动: 【{platform_name}】QMT 客户端")
    print("=" * 60)

    if not os.path.exists(exe_path):
        print(f"[错误] 未找到客户端文件: {exe_path}")
        return False

    if is_process_running(exe_path):
        print(f"[✓] 检测到【{platform_name}】QMT 已经在运行中，跳过启动与登录。")
        if auto_minimize:
            for proc in psutil.process_iter(['name', 'exe', 'pid']):
                try:
                    if proc.info.get('name', '').lower() == Path(exe_path).name.lower():
                        minimize_qmt_windows(platform_name=platform_name, root_pid=proc.info['pid'])
                        break
                except Exception:
                    pass
        return True

    print(f"[*] 步骤 1/4: 正在拉起客户端进程: {exe_path}")
    qmt_dir = os.path.dirname(exe_path)
    proc = subprocess.Popen([exe_path], cwd=qmt_dir)
    print(f"[*] 进程已创建 (PID: {proc.pid})")

    # 步骤 2: 等待检查更新完成
    print(f"[*] 步骤 2/4: 等待客户端检查更新并弹出登录界面...")
    countdown(wait_update_check, f"{platform_name} 检查更新中")

    # 步骤 3: 触发自动登录
    print(f"[*] 步骤 3/4: 触发登录...")
    trigger_qmt_login(platform_name=platform_name, proc_pid=proc.pid, timeout=10)

    # 步骤 4: 等待登录验证及交易主窗口加载
    print(f"[*] 步骤 4/4: 等待登录验证及数据加载完成...")
    countdown(wait_after_login, f"{platform_name} 正在登录并加载")

    # 步骤 5: 登录完成后自动最小化
    if auto_minimize:
        minimize_qmt_windows(platform_name=platform_name, root_pid=proc.pid)

    print(f"[✓✓] 【{platform_name}】QMT 启动、登录及最小化流程完成！")
    return True


def start_tdx_app(
    exe_path: str,
    wait_auto_login: int = 10,
    auto_minimize: bool = True
) -> bool:
    """
    启动通达信 (TdxW.exe)：
    通达信已设置自动登录，只需要：
    1. 拉起进程
    2. 等待 10 秒自动登录完成
    3. 叉掉自动登录后弹出的广告
    4. 最小化
    """
    platform_name = "通达信"
    print(f"\n" + "=" * 60)
    print(f"  >>> 【串行流程】正在启动: 【{platform_name}】行情客户端")
    print("=" * 60)

    if not os.path.exists(exe_path):
        print(f"[错误] 未找到通达信客户端文件: {exe_path}")
        return False

    if is_process_running(exe_path):
        print(f"[✓] 检测到【{platform_name}】已经在运行中，跳过启动。")
        if auto_minimize:
            for proc in psutil.process_iter(['name', 'exe', 'pid']):
                try:
                    if proc.info.get('name', '').lower() == Path(exe_path).name.lower():
                        minimize_qmt_windows(platform_name=platform_name, root_pid=proc.info['pid'])
                        break
                except Exception:
                    pass
        return True

    print(f"[*] 步骤 1/3: 正在拉起通达信进程: {exe_path}")
    tdx_dir = os.path.dirname(exe_path)
    proc = subprocess.Popen([exe_path], cwd=tdx_dir)
    print(f"[*] 进程已创建 (PID: {proc.pid})")

    # 步骤 2: 等待自动登录完成
    print(f"[*] 步骤 2/3: 等待通达信自动登录完成...")
    countdown(wait_auto_login, f"{platform_name} 自动登录中")

    # 步骤 3: 叉掉登录后弹出的广告
    print(f"[*] 步骤 3/3: 检测并关闭广告弹窗...")
    close_tdx_qrcode_window(proc_pid=proc.pid, timeout=5)

    # 最小化
    if auto_minimize:
        time.sleep(0.5)
        minimize_qmt_windows(platform_name=platform_name, root_pid=proc.pid)

    print(f"[✓✓] 【{platform_name}】启动、自动登录、广告关闭及最小化完成！")
    return True

