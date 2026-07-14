# coding=utf-8
import logging

import requests
from bs4 import BeautifulSoup
import re
from decimal import Decimal, ROUND_HALF_UP
import random
import json
from helper import utils
import time
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)

class StopStreamException(Exception):
    pass

# Retrieve the latest net worth and timestamp of off-market funds
def get_last_net_worth(stock_code):
    try:
        url = f"https://fund.eastmoney.com/{str(stock_code)}.html"
        response = build_and_get(url)
        code = 200
        msg = "Successfully retrieved"
        soup = BeautifulSoup(response.text, 'html.parser')

        bonus_date = None
        bonus_money = float('0')

        # Parse dividend payout data
        if soup.find('li', {'class': 'position_bonus'}):
            if soup.find('li', {'class': 'position_bonus'}).find("table", {'class': 'ui-table-hover'}):
                if soup.find('li', {'class': 'position_bonus'}).find("table", {'class': 'ui-table-hover'}).find('tr'):
                    bonus_arr = soup.find('li', {'class': 'position_bonus'}).find("table", {'class': 'ui-table-hover'}).find('tr').find_all('td')
                    match = re.search(r'\d+\.?\d*', bonus_arr[1].text)
                    if match:
                        bonus_date = bonus_arr[0].text
                        bonus_money = float(match.group())

        # Parse unit net worth
        dl_blocks = soup.find('div', {'class': 'dataOfFund'}).find_all('dl', class_=re.compile(r'^dataItem0'))

        i = 1
        for dl_block in dl_blocks:
            if '单位净值' in dl_block.text:
                break
            i += 1

        detail_soup = soup.find('div', {'class': 'dataOfFund'}).find('dl', {'class': 'dataItem0'+str(i)})
        net_worth = detail_soup.find('dd', {'class': 'dataNums'}).find('span').text
        if isinstance(net_worth, (int, float)):
            code = 400
            msg = "Failed to extract net worth"

        match = re.search(r'\((\d{4}-\d{2}-\d{2})\)', detail_soup.text)
        if match:
            net_worth_date = match.group(1)
        else:
            net_worth_date = ''
            code = 400
            msg = "Failed to extract net worth date via regex"

        return {'code': code, 'msg': msg, 'net_worth_date': net_worth_date, 'net_worth': net_worth, 'bonus_date': bonus_date,
                'bonus_money': bonus_money}
    except Exception as e:
        logging.error(f"Error parsing net worth for {stock_code}: {e}")

def get_target_index(code):
    url = f"https://fund.eastmoney.com/{str(code)}.html"
    response = build_and_get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    td_tag = soup.find('td', {'class': 'specialData'})
    print(f"Fetching fund data for code: {code}")
    raw_text = td_tag.get_text(strip=True)
    parts = raw_text.split('|')
    # Extract tracking target index (content after the colon)
    target_index = parts[0].split('：')[1].strip()
    # Extract annualized tracking error (content after the colon)
    annual_error = parts[1].split('：')[1].strip()
    # 5. Return result
    return {"target_index": target_index, "annual_error": annual_error}

def build_and_get(url, stream = False):
    USER_AGENT_LIST = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.122 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Safari/605.1.15",
    ]
    headers = {
        'User-Agent': random.choice(USER_AGENT_LIST)
    }
    response = requests.get(url, headers=headers, timeout=10)
    response.encoding = 'utf-8'
    return response


def stream_listener(index_code, cookie, process_func=lambda x, index_code: None):
    derive = utils.get_derive_by_code(index_code)
    if derive == -1:
        return None

    url = f"https://1.push2.eastmoney.com/api/qt/stock/sse?fields=f58,f734,f107,f57,f43,f59,f169,f170,f152,f46,f60,f44,f45,f47,f48,f19,f17,f531,f15,f13,f11,f20,f18,f16,f14,f12,f39,f37,f35,f33,f31,f40,f38,f36,f34,f32,f211,f212,f213,f214,f215,f210,f209,f208,f207,f206,f161,f49,f171,f50,f86,f168,f108,f167,f71,f292,f51,f52,f191,f192,f452,f177&secid={str(derive)}.{str(index_code)}"

    # Construct request headers
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en,zh-CN;q=0.9,zh;q=0.8",
        "Connection": "keep-alive",
        "Cookie": cookie,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"'
    }

    while True:
        try:
            with requests.get(url, headers=headers, timeout=10, stream=True) as resp:
                if resp.status_code != 200:
                    logger.error(f"Abnormal HTTP status code: {resp.status_code}. Cookie may have expired.")
                    time.sleep(5)
                    continue

                logger.info(f"Connected successfully, [{index_code}]. Starting stream listener...")
                for line in resp.iter_lines(decode_unicode=True):
                    if line:
                        process_func(line, index_code)

        except RequestException as e:
            logger.error(f"Connection error: {e}. Retrying in 5 seconds...")
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info("\nStream listener terminated by user.")
            break
        except Exception as e:
            logger.error(f"Processing error: {e}. Continuing listener stream...")

def fetch_single_snapshot_safe(index_code, cookie):
    """
    Safe single-snapshot retrieval: fetch data and immediately terminate the TCP connection to prevent IP bans.
    """
    # Note: You should check your spider.stream_listener implementation,
    # copy the actual URL and headers layout here.
    # Below is a standard placeholder example targeting Eastmoney's API:
    derive = utils.get_derive_by_code(index_code)
    if derive == -1:
        return None
    url = f"https://1.push2.eastmoney.com/api/qt/stock/sse?fields=f58,f734,f107,f57,f43,f59,f169,f170,f152,f46,f60,f44,f45,f47,f48,f19,f17,f531,f15,f13,f11,f20,f18,f16,f14,f12,f39,f37,f35,f33,f31,f40,f38,f36,f34,f32,f211,f212,f213,f214,f215,f210,f209,f208,f207,f206,f161,f49,f171,f50,f86,f168,f108,f167,f71,f292,f51,f52,f191,f192,f452,f177&secid={str(derive)}.{str(index_code)}"

    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/142.0.0.0",
    }
    try:
        # Key 1: stream=True enables reading line-by-line without waiting for the connection to terminate (long-lived connections)
        # Key 2: Using the 'with' statement guarantees response.close() is strictly invoked upon exiting scope
        with requests.get(url, headers=headers, stream=True, timeout=5) as response:
            # Validate HTTP status code
            if response.status_code != 200:
                logger.warning(f"Failed to retrieve snapshot for {index_code}. HTTP status code: {response.status_code}")
                return None
            # Read response stream line-by-line
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8').strip()
                    # Handle SSE format "data: " prefix compatibility
                    if decoded_line.startswith('data: '):
                        decoded_line = decoded_line[6:]
                    # Parse JSON
                    try:
                        data_dict = json.loads(decoded_line)
                        # Verify if the payload is valid snapshot data
                        if "data" in data_dict and "f43" in data_dict["data"]:
                            # Successfully retrieved snapshot!
                            # Return statement terminates the 'with' block, instantly closing the TCP connection
                            return data_dict
                    except json.JSONDecodeError:
                        continue
    except requests.exceptions.Timeout:
        logger.error(f"安全抓取 {index_code} 时发生异常: {e}")
    return None

