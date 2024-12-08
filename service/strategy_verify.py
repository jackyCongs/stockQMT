# coding=utf-8

from service import history_download
from helper import utils
from db import market_data
import threading
from tqdm import tqdm


# 校验数据收益率
class ThreadRunner(threading.Thread):
    def __init__(self, db, code, result_map):
        threading.Thread.__init__(self)
        self.db = db
        self.code = code
        self.result_map = result_map

    def run(self):
        stock_code = utils.enhance_stock_code(self.code)
        start_time = '202001010000'
        transaction_count = 0
        initial_capital = 10000
        current_capital = initial_capital

        while True:
            stock_day_start = market_data.find_next_data(self.db, stock_code, start_time)
            if stock_day_start is None:
                break
            if stock_day_start['time'] >= '20240930':
                break
            limit_up_price = round(stock_day_start['preClose'] * 1.1, 2)
            stock_day_end = market_data.find_data(self.db, stock_code, stock_day_start['time'][:-4] + "1500")

            if stock_day_end['high'] >= limit_up_price and stock_day_end['low'] < limit_up_price:
                next_day = market_data.find_next_data(self.db, stock_code, stock_day_start['time'][:-4] + "1500")
                if next_day is not None:
                    transaction_count += 1
                    current_profit = round((next_day['open'] - stock_day_end['high']) / stock_day_end['high'] * 100, 6)
                    current_capital = round(current_capital * (1 + current_profit / 100), 2)

                    print(f"{stock_code} - {stock_day_end['time']}以{stock_day_end['high']}买入，次日{next_day['open']}卖出，"
                          f"收益率{current_profit}%, 当前资金{current_capital}")

            start_time = stock_day_end['time']

        # 将每个线程的结果存储到result_map中
        self.result_map[stock_code] = (current_capital, transaction_count)


def run(db):
    code_list = history_download.get_code_lists()
    result_map = {}  # 存储每个代码的结果和交易次数

    pbar = tqdm(total=len(code_list), desc="verifying...", mininterval=0.1)

    threads = []
    for code in code_list:
        pbar.update(1)
        if code.startswith("3") or code.startswith("688"):
            continue
        thread = ThreadRunner(db, code, result_map)
        threads.append(thread)
        thread.start()

        if len(threads) >= 100:
            for t in threads:
                t.join()
            threads = []
    # 循环结束后，确保所有剩余线程都已完成
    for t in threads:
        t.join()

    pbar.close()

    total_value = 0
    total_transaction_count = 0
    valid_stock_count = 0
    for code, (value, transaction_count) in result_map.items():
        if transaction_count == 0:
            continue
        valid_stock_count += 1
        print(f"股票代码：{code}, 资产价值：{value}元，收益率{round((value - 10000) / 10000 * 100, 2)}%")
        total_value += value
        total_transaction_count += transaction_count

    print(f"总交易{total_transaction_count}次，有交易的stock数量{valid_stock_count}, 综合平均收益率: "
          f"{round((total_value - (valid_stock_count * 10000)) / (valid_stock_count * 10000) * 100, 4)}%")
    exit()
