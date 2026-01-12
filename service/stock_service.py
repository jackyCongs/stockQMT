# coding=utf-8
import time
import logging
from xtquant import xtdata
import helper.utils
from db import stock
from helper import utils


def read_lines_to_array(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            # 读取所有行并去除每行末尾的换行符
            lines = [line.strip().strip('"') for line in file]
            return lines
    except FileNotFoundError:
        logger.error(f"错误：文件 '{file_path}' 不存在")
        return []
    except Exception as e:
        logger.error(f"错误：读取文件时发生异常: {e}")
        return []

logger = logging.getLogger(__name__)

class StockService:
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
        batch_size = 50
        for i in range(0, len(self.format_codes), batch_size):
            batch_codes = self.format_codes[i: i + batch_size]
            seq = xtdata.subscribe_whole_quote(batch_codes, callback=self.stocks_handler)
            logger.info(f"批次 {i // batch_size + 1} 订阅成功，包含 {len(batch_codes)} 只股票，订阅ID: {seq}")
            time.sleep(2)
        time.sleep(1)
        logger.info("所有批次订阅请求发送完毕")

    def stocks_handler(self, msgs):
        update_data = []
        for code in msgs:
            # stock.update_stock_price(self.db, msgs[code]['lastPrice'], helper.utils.purified_code(code))
            update_data.append((helper.utils.purified_code(code), msgs[code]['lastPrice']))
        # 2. 调用批量更新函数
        if update_data:
            stock.batch_update_stock_price(self.db, update_data)
            logger.info(f"Batch update successful: {len(update_data)} stocks")
        logger.info(f"{len(update_data)} 条数据 updated successful")


