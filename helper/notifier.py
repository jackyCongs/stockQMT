import requests
import logging
import time

logger = logging.getLogger(__name__)

TG_BOT_TOKEN = '8532693435:AAHqYr_XV4Sy6_ICDZESrJ3IKeEU01uHNsA'

TG_CHAT_ID = '8498114474'

PROXIES = {
    "http": "http://127.0.0.1:7897",
}


# ===========================================

def send_telegram_alert(title, content, max_retries=3):
    """
    发送 Telegram 报警消息 (带重试机制)
    :param title: 标题
    :param content: 内容
    :param max_retries: 最大重试次数，默认3次
    """
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"

    # 构造漂亮的 HTML 格式消息
    text = f"<b>🚨 {title}</b>\n\n{content}"

    data = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    # 重试循环
    for attempt in range(1, max_retries + 1):
        print("开始发送消息通知...")
        try:
            # 设置 10秒 超时，防止卡住主线程
            resp = requests.post(url, data=data, proxies=PROXIES, timeout=10)

            if resp.status_code == 200:
                logger.info(f"Telegram 报警发送成功 (第{attempt}次尝试)")
                return True  # 发送成功，直接退出函数
            else:
                logger.warning(f"Telegram 发送失败 (HTTP {resp.status_code}): {resp.text}")

        except requests.exceptions.RequestException as e:
            logger.warning(f"Telegram 连接异常 (第{attempt}/{max_retries}次): {e}")

        # 如果不是最后一次尝试，就等待几秒再试
        if attempt < max_retries:
            time.sleep(1)

            # 如果循环结束还没成功
    logger.error("Telegram 报警最终发送失败，已达到最大重试次数！")
    return False