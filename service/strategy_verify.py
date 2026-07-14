# coding=utf-8

from service import history_download
from helper import utils
from db import market_data
import threading
from tqdm import tqdm

'''
Experimental Records:
    1. Late-session limit up strategy, yield: -1.693%
    2. Limit down strategy

'''


# Verify data yield / return metrics
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
            # Calculate first data point for each day. Ensure start_time ends with 1500.
            # i is the number of consecutive limit downs
            i = 0
            stock_day_start = market_data.find_next_data(self.db, stock_code, start_time)
            if stock_day_start is None:
                break
            if stock_day_start['time'] >= '20240930':
                break
            # Close data for this day. Get time to fetch the next day in the loop.
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
                    # Whether the limit-down opened during the session
                    is_open = False
                    for three_day in next_all_day_data:
                        if three_day['high'] > limit_down_price and three_day['low'] <= limit_down_price:
                            is_open = True
                            break
                    # Limit down for 2 consecutive days, buy on 3rd day limit-down trigger, sell on 4th day close.
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
                            f"{stock_code} - Bought at {limit_down_price} on {next_day_start['time'][:8]}, sold next day at {next_next_day_end['close']}, "
                            f"yield {current_profit}%, current capital {current_capital}")
                    else:
                        break
            else:
                i = 0
            start_time = stock_day_end['time']

        # Save results of each thread in result_map
        self.result_map[stock_code] = (current_capital, transaction_count)


def run(db):
    code_list = history_download.get_code_lists()
    result_map = {}  # Store results and transaction counts for each ticker

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
    # Ensure all remaining threads are joined after the loop completes
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
        print(f"Stock Code: {code}, Asset Value: {value} CNY, Yield: {round((value - 10000) / 10000 * 100, 2)}%")
        total_value += value
        total_transaction_count += transaction_count

    print(f"Total Transactions: {total_transaction_count}, Traded Stock Count: {valid_stock_count}, Overall Average Yield: "
          f"{round((total_value - (valid_stock_count * 10000)) / (valid_stock_count * 10000) * 100, 4)}%")
    exit()
