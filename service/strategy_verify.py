# coding=utf-8

from service import history_download
from helper import utils
from db import market_data
import threading
from tqdm import tqdm

'''
实验记录：
    1、尾盘涨停战法，收益率：-1.693%
    2、跌停战法

'''


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
            # 计算每天第一条数据，要保证 start_time 1500结尾
            # i 是连板的次数
            i = 0
            stock_day_start = market_data.find_next_data(self.db, stock_code, start_time)
            if stock_day_start is None:
                break
            if stock_day_start['time'] >= '20240930':
                break
            # 这天收盘的数据，获取时间，用来后续循环获取下一天用
            stock_day_end = market_data.find_data(self.db, stock_code, stock_day_start['time'][:-4] + "1500")

            limit_up_price = round(stock_day_start['preClose'] * 1.1, 2)
            limit_down_price = round(stock_day_start['preClose'] * 0.9, 2)
            if stock_day_end['close'] <= limit_down_price:
                i += 1
                if i == 2:
                    next_day_start = market_data.find_next_data(self.db, stock_code,
                                                                stock_day_start['time'][:-4] + "1500")
                    limit_down_price = round(next_day_start['preClose'] * 0.9, 2)
                    next_all_day_data = market_data.get_all_day(self.db, stock_code, next_day_start['time'])
                    if next_all_day_data is None:
                        break
                    # 是否开过板
                    is_open = False
                    for three_day in next_all_day_data:
                        if three_day['high'] > limit_down_price and three_day['low'] <= limit_down_price:
                            is_open = True
                            break
                    # 连续两天跌停，第三题触发跌停后买入，第4天收盘卖出
                    next_next_day_start = market_data.find_next_data(self.db, stock_code,
                                                                next_day_start['time'][:-4] + "1500")
                    if next_next_day_start is not None and is_open:
                        next_next_day_end = market_data.find_data(self.db, stock_code,
                                                         next_next_day_start['time'][:-4] + "1500")
                        transaction_count += 1
                        current_profit = round((next_next_day_end['close'] - limit_down_price) / limit_down_price * 100,
                                               6)
                        current_capital = round(current_capital * (1 + current_profit / 100) * (1 - (1+1+5)/10000), 2)

                        print(
                            f"{stock_code} - {next_day_start['time'][:8]}以{limit_down_price}买入，次日{next_next_day_end['close']}卖出，"
                            f"收益率{current_profit}%, 当前资金{current_capital}")
                    else:
                        break
            else:
                i = 0
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
