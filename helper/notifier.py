import requests
import logging
import time

logger = logging.getLogger(__name__)

# ============ Bark 配置 ============
BARK_KEY = 'D2XvyLTYAsW3VHEmgMSyz5'
BARK_SERVER = 'https://api.day.app'

# ============ 旧的 Telegram 配置 (已停用) ============
# TG_BOT_TOKEN = '8532693435:AAHqYr_XV4Sy6_ICDZESrJ3IKeEU01uHNsA'
# TG_CHAT_ID = '8498114474'
# PROXIES = {
#     "http": "http://127.0.0.1:7897",
# }


# ===========================================

def send_bark_alert(title, content, max_retries=3):
    """
    发送 Bark 推送通知到 iPhone (带重试机制)
    :param title: 标题
    :param content: 内容
    :param max_retries: 最大重试次数，默认3次
    """
    url = f"{BARK_SERVER}/{BARK_KEY}"

    data = {
        "title": f"🚨 {title}",
        "body": content,
        "sound": "alarm",       # 使用报警铃声，确保注意到
        "group": "stockQMT",    # 消息分组，方便管理
        "isArchive": "1",       # 自动保存到历史记录
    }

    # 重试循环
    for attempt in range(1, max_retries + 1):
        print("开始发送 Bark 消息通知...")
        try:
            # 设置 10秒 超时，防止卡住主线程
            resp = requests.post(url, json=data, timeout=10)

            if resp.status_code == 200:
                result = resp.json()
                if result.get("code") == 200:
                    logger.info(f"Bark 报警发送成功 (第{attempt}次尝试)")
                    return True  # 发送成功，直接退出函数
                else:
                    logger.warning(f"Bark 发送失败: {result}")
            else:
                logger.warning(f"Bark 发送失败 (HTTP {resp.status_code}): {resp.text}")

        except requests.exceptions.RequestException as e:
            logger.warning(f"Bark 连接异常 (第{attempt}/{max_retries}次): {e}")

        # 如果不是最后一次尝试，就等待几秒再试
        if attempt < max_retries:
            time.sleep(1)

    # 如果循环结束还没成功
    logger.error("Bark 报警最终发送失败，已达到最大重试次数！")
    return False


# 向后兼容：保留旧函数名，所有现有代码调用 send_telegram_alert() 无需任何修改
send_telegram_alert = send_bark_alert


if __name__ == '__main__':
    send_bark_alert("测试", "这是一条来自 stockQMT 的测试通知")