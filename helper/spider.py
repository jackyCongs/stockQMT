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

# 获取场外基金最新的净值和净值时间
def get_last_net_worth(stock_code):
    try:
        url = f"https://fund.eastmoney.com/{str(stock_code)}.html"
        response = build_and_get(url)
        code = 200
        msg = "获取成功"
        soup = BeautifulSoup(response.text, 'html.parser')

        bonus_date = None
        bonus_money = float('0')

        # 解析分红数据
        if soup.find('li', {'class': 'position_bonus'}):
            if soup.find('li', {'class': 'position_bonus'}).find("table", {'class': 'ui-table-hover'}):
                if soup.find('li', {'class': 'position_bonus'}).find("table", {'class': 'ui-table-hover'}).find('tr'):
                    bonus_arr = soup.find('li', {'class': 'position_bonus'}).find("table", {'class': 'ui-table-hover'}).find('tr').find_all('td')
                    match = re.search(r'\d+\.?\d*', bonus_arr[1].text)
                    if match:
                        bonus_date = bonus_arr[0].text
                        bonus_money = float(match.group())

        # 解析单位净值
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
            msg = "净值提取失败"

        match = re.search(r'\((\d{4}-\d{2}-\d{2})\)', detail_soup.text)
        if match:
            net_worth_date = match.group(1)
        else:
            net_worth_date = ''
            code = 400
            msg = "净值日期正则提取失败"

        return {'code': code, 'msg': msg, 'net_worth_date': net_worth_date, 'net_worth': net_worth, 'bonus_date': bonus_date,
                'bonus_money': bonus_money}
    except Exception as e:
        logging.error(f"解析净值错误: {stock_code}: {e}")

def get_target_index(code):
    url = f"https://fund.eastmoney.com/{str(code)}.html"
    response = build_and_get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    td_tag = soup.find('td', {'class': 'specialData'})
    print(f"get {code}")
    raw_text = td_tag.get_text(strip=True)
    parts = raw_text.split('|')
    # 提取跟踪标的（冒号后内容）
    target_index = parts[0].split('：')[1].strip()
    # 提取年化跟踪误差（冒号后内容）
    annual_error = parts[1].split('：')[1].strip()
    # 5. 输出结果
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

    # 构建请求头
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
                    logger.error(f"状态码异常: {resp.status_code}，可能Cookie过期")
                    time.sleep(5)
                    continue

                logger.info(f"连接成功，[{index_code}], 开始监听... ")
                for line in resp.iter_lines(decode_unicode=True):
                    if line:
                        process_func(line, index_code)

        except RequestException as e:
            logger.error(f"连接错误: {e}，5秒后重试")
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info("\n用户终止监听")
            break
        except Exception as e:
            logger.error(f"处理错误: {e}，继续监听")

def fetch_single_snapshot_safe(index_code, cookie):
    """
    极其安全的单次快照获取：拿完即走，强制断开 TCP 连接，防封 IP。
    """
    # ⚠️ 注意：你需要去你的 spider.stream_listener 源码里，
    # 把请求第三方数据的真实 URL 和 Headers 格式抄过来放到这里。
    # 这里我写个通用的东方财富接口推测示例：
    derive = utils.get_derive_by_code(index_code)
    if derive == -1:
        return None
    url = f"https://1.push2.eastmoney.com/api/qt/stock/sse?fields=f58,f734,f107,f57,f43,f59,f169,f170,f152,f46,f60,f44,f45,f47,f48,f19,f17,f531,f15,f13,f11,f20,f18,f16,f14,f12,f39,f37,f35,f33,f31,f40,f38,f36,f34,f32,f211,f212,f213,f214,f215,f210,f209,f208,f207,f206,f161,f49,f171,f50,f86,f168,f108,f167,f71,f292,f51,f52,f191,f192,f452,f177&secid={str(derive)}.{str(index_code)}"

    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    }
    try:
        # 关键 1：stream=True 允许我们按行读取，而不是等整个长连接结束（长连接永远不会结束）
        # 关键 2：使用 with 语句，保证离开作用域时 response.close() 被绝对调用！
        with requests.get(url, headers=headers, stream=True, timeout=5) as response:
            # 校验 HTTP 状态码
            if response.status_code != 200:
                logger.warning(f"获取 {index_code} 失败，HTTP 状态码: {response.status_code}")
                return None
            # 逐行读取数据流
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8').strip()
                    # 兼容 SSE 格式的 "data: " 前缀
                    if decoded_line.startswith('data: '):
                        decoded_line = decoded_line[6:]
                    # 解析 JSON
                    try:
                        data_dict = json.loads(decoded_line)
                        # 校验是否为有效快照数据
                        if "data" in data_dict and "f43" in data_dict["data"]:
                            # 成功拿到！
                            # return 会立刻跳出 with 语句块，requests 会在这瞬间光速掐断 TCP 连接
                            return data_dict
                    except json.JSONDecodeError:
                        continue
    except requests.exceptions.Timeout:
        logger.warning(f"获取 {index_code} 超时，已安全断开连接")
    except Exception as e:
        logger.error(f"安全抓取 {index_code} 时发生异常: {e}")
    return None

