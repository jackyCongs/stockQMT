# coding=utf-8

import requests
from bs4 import BeautifulSoup
import re
from decimal import Decimal, ROUND_HALF_UP
import random
import json


# 获取场外基金最新的净值和净值时间
def get_last_net_worth(code):
    url = f"https://fund.eastmoney.com/{str(code)}.html"
    response = build_and_get(url)
    code = 200
    msg = "获取成功"
    soup = BeautifulSoup(response.text, 'html.parser')

    bonus_date = None
    bonus_money = Decimal('0')

    # 解析分红数据
    if soup.find('li', {'class': 'position_bonus'}):
        if soup.find('li', {'class': 'position_bonus'}).find("table", {'class': 'ui-table-hover'}):
            if soup.find('li', {'class': 'position_bonus'}).find("table", {'class': 'ui-table-hover'}).find('tr'):
                bonus_arr = soup.find('li', {'class': 'position_bonus'}).find("table", {'class': 'ui-table-hover'}).find('tr').find_all('td')
                match = re.search(r'\d+\.?\d*', bonus_arr[1].text)
                if match:
                    bonus_date = bonus_arr[0].text
                    bonus_money = Decimal(match.group())

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


def get_stock_bid_sell_info(stock_code):
    derive = stock_code.stock_code.split('.')[1]
    derive_num = 0
    if derive == "SZ":
        derive_num = 0
    elif derive == "SH":
        derive_num = 1
    else:
        return None
    url = f"https://1.push2.eastmoney.com/api/qt/stock/sse?fields=f58,f734,f107,f57,f43,f59,f169,f170,f152,f46,f60,f44,f45,f47,f48,f19,f17,f531,f15,f13,f11,f20,f18,f16,f14,f12,f39,f37,f35,f33,f31,f40,f38,f36,f34,f32,f211,f212,f213,f214,f215,f210,f209,f208,f207,f206,f161,f49,f171,f50,f86,f168,f108,f167,f71,f292,f51,f52,f191,f192,f452,f177&secid={str(derive_num)}.{derive[0]}"
    response = build_and_get(url, True)
    response.raise_for_status()  # 确保请求成功
    try:
        for line in response.iter_lines():  # 迭代每一行数据
            decoded_line = line.decode('utf-8')
            data = json.loads(decoded_line.replace('data: ', ''))
            return {'sell_1_price': data['data']['f39'], 'sell_1_num': data['data']['f40'], 'buy_1_price': data['data']['f19'], 'buy_1_num': data['data']['f20']}
    except requests.exceptions.RequestException as e:
        print(f"请求失败：{e}")
    finally:
        response.close()


def build_and_get(url, stream = False):
    USER_AGENT_LIST = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.122 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Safari/605.1.15",
    ]
    headers = {
        'User-Agent': random.choice(USER_AGENT_LIST)
    }
    if stream :
        response = requests.get(url, headers=headers, timeout=10, stream=True)
    else:
        response = requests.get(url, headers=headers, timeout=10)
    response.encoding = 'utf-8'
    return response
