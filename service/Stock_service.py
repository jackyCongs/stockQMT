# coding=utf-8
import time

import pymysql
from xtquant import xtdata

import helper.utils
from db import stock

from helper import utils

db_config = {
    'host': '47.104.167.147',
    'user': 'root',
    'password': '211314ok',
    'database': 'bill',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}


def read_lines_to_array(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            # 读取所有行并去除每行末尾的换行符
            lines = [line.strip().strip('"') for line in file]
            return lines
    except FileNotFoundError:
        print(f"错误：文件 '{file_path}' 不存在")
        return []
    except Exception as e:
        print(f"错误：读取文件时发生异常: {e}")
        return []


class Stock_service:
    def __init__(self, db):
        self.format_codes = []
        self.stock_codes = read_lines_to_array("./files/prepared_update_stock_codes.txt")
        self.format_code()
        self.db = db

    def format_code(self):
        for code in self.stock_codes:
            if utils.enhance_stock_code(code) == code:
                continue
            self.format_codes.append(utils.enhance_stock_code(code))

    def update_stock_price(self):
        subscribe_id = xtdata.subscribe_whole_quote(self.format_codes, callback=self.stocks_handler)
        print(subscribe_id)
        time.sleep(30)
        print("executed over")

    def stocks_handler(self, msgs):
        for code in msgs:
            stock.update_stock_price(self.db, msgs[code]['lastPrice'], helper.utils.purified_code(code))
            print(f"{code} updated successful")


