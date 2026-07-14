import requests
import logging
import time

logger = logging.getLogger(__name__)

# ============ Bark Configuration ============
BARK_KEY = 'D2XvyLTYAsW3VHEmgMSyz5'
BARK_SERVER = 'https://api.day.app'

# ============ Deprecated Telegram Configuration (Disabled) ============
# TG_BOT_TOKEN = '8532693435:AAHqYr_XV4Sy6_ICDZESrJ3IKeEU01uHNsA'
# TG_CHAT_ID = '8498114474'
# PROXIES = {
#     "http": "http://127.0.0.1:7897",
# }


# ===========================================

def send_bark_alert(title, content, max_retries=3):
    """
    Send Bark push notification to iPhone (with retry mechanism).
    :param title: Message title
    :param content: Message body content
    :param max_retries: Maximum retry attempts, defaults to 3
    """
    url = f"{BARK_SERVER}/{BARK_KEY}"

    data = {
        "title": f"🚨 {title}",
        "body": content,
        "sound": "alarm",       # Use alarm sound to ensure alert is noticed
        "group": "stockQMT",    # Message group for easy management
        "isArchive": "1",       # Automatically archive message in history log
    }

    # Retry loop
    for attempt in range(1, max_retries + 1):
        print("Sending Bark push notification...")
        try:
            # Set 10-second timeout to avoid blocking the main execution thread
            resp = requests.post(url, json=data, timeout=10)

            if resp.status_code == 200:
                result = resp.json()
                if result.get("code") == 200:
                    logger.info(f"Bark alert sent successfully (Attempt {attempt})")
                    return True  # Exit early on successful transmission
                else:
                    logger.warning(f"Bark delivery failed: {result}")
            else:
                logger.warning(f"Bark delivery failed (HTTP {resp.status_code}): {resp.text}")

        except requests.exceptions.RequestException as e:
            logger.warning(f"Bark connection exception (Attempt {attempt}/{max_retries}): {e}")

        # Wait a moment before retrying if there are remaining attempts
        if attempt < max_retries:
            time.sleep(1)

    # If retry loop exhausted without success
    logger.error("Bark alert delivery failed. Maximum retry limit reached.")
    return False


# Backward compatibility: Retain the deprecated telegram function name alias
send_telegram_alert = send_bark_alert


if __name__ == '__main__':
    send_bark_alert("Test Alert", "This is a test notification from stockQMT")