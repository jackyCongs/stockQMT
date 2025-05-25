# coding=utf-8

import pymysql
from db import strategy_flows
import math
import pandas as pd
import configparser

db_config = {
    'host': '47.104.167.147',
    'user': 'root',
    'password': '211314ok',
    'database': 'bill',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}


def load_config():
    config = configparser.ConfigParser()
    config.read('config.ini')
    if config['FLOWS']['file'] is None or config['FLOWS']['file'] == "":
        exit("文件名存在异常")
    return config

class Trans_flows:
    def __init__(self, cash):
        self.cash = cash
        self.available_balance = 0
        self.connection = pymysql.connect(**db_config)
        # 关闭自动提交，开启以天为单位的事务，要么全部成功要么全部失败
        self.connection.autocommit(False)
        self.config = load_config()

    def check_result(self):
        if self.available_balance != self.cash:
            exit(f"对账不通过，账本余额和实际余额不一致: accounting: {self.available_balance}, cash: {self.cash}")
        print(f"对账通过: 当前可用余额: {self.available_balance}")

    def run(self):
        last_flows = strategy_flows.get_by_max_flows_sequence(self.connection)
        last_trans_date = strategy_flows.get_max_flows_trans_date(self.connection)
        if last_flows is None or last_trans_date is None:
            exit("初始化错误!")

        self.available_balance = float(last_flows['remained_amount'])
        last_update_date = int(last_trans_date)
        df = pd.read_excel(self.config['FLOWS']['file'], engine='openpyxl')  # 如果文件是.xlsx格式
        # df = pd.read_csv(self.config['FLOWS']['file'], encoding="latin1")
        current_process_date = 0

        try:
            for i, (index, row) in enumerate(df.iterrows(), start=1):
                # 过滤掉已经更新过的日期的数据
                if row['日期'] <= last_update_date:
                    continue

                if row['日期'] != current_process_date:
                    if current_process_date != 0:
                        self.connection.commit()

                    current_process_date = row['日期']

                last_flows = strategy_flows.get_by_max_flows_sequence(self.connection)
                next_sequence = last_flows['flows_sequence'] + 1

                # print(f"第{i}行: 证券代码={row['证券代码']}, 买卖={row['买卖']},操作={row['操作']}, 成交数量={row['成交数量']}, 成交价格={row['成交价格']}, 成交金额={row['成交金额']}, 资金余额={row['资金余额']}, 成交序号={row['成交序号']}, 证券名称={row['证券名称']}, 成交数量={row['成交数量']}, 手续费={row['手续费']}, 日期={row['日期']}, 成交时间={row['成交时间']}, 市场类型={row['市场类型']}")
                hour = 9
                if len(str(row['成交时间'])) == 6:
                    hour = int(str(row['成交时间'])[:2])
                # 赎回交易
                if hour >= 16:
                    # 要识别是开始赎回，还是赎回到账
                    if row['成交金额'] == 0:
                        # 开始赎回
                        if row['成交数量'] == 0:
                            # 成交金额空，数量也空没有实际意义，直接忽略
                            continue
                        if strategy_flows.get_flow_by_trans_sequence(self.connection, row['成交序号']):
                            print(f"成交序号: {row['成交序号']} 已经存在，处理忽略")
                            continue
                        strategy_flows.insert_strategy_flow(self.connection,
                                             {"stock_code": row['证券代码'], "stock_name": row['证券名称'], "type": "开放基金赎回",
                                              "num": -math.fabs(row['成交数量']), "trans_date": row['日期'],
                                              "trans_sequence": row['成交序号'], "status": 0})
                    else:
                        # 赎回到账
                        # trans_sequence 如果已经存在了就忽略
                        trans_sequence_flow = strategy_flows.get_flow_by_trans_sequence(self.connection, row['成交序号'])
                        if trans_sequence_flow and trans_sequence_flow['status'] == 1:
                            print(f"成交序号: {row['成交序号']} 已经存在，处理忽略")
                            continue
                        incomplete_flow = strategy_flows.get_incomplete_flow_by_stock_code(self.connection, row['证券代码'])
                        other_fee = self.check_redemption(incomplete_flow, row)
                        changed_amount = round(row['成交金额'] - other_fee, 2)
                        strategy_flows.update_strategy_flow(self.connection,
                                             {"price": row['成交价格'], "commission": 0, "other_fee": other_fee,
                                              "amount_changed": changed_amount,
                                              "remained_amount": row['资金余额'], "flows_sequence": next_sequence,
                                              "trans_sequence": row['成交序号'], "status": 1}, incomplete_flow['id'])
                else:
                    # 正常交易
                    self.check_common_transaction(row['成交数量'], row['成交价格'], row['手续费'], row['成交金额'], row['资金余额'])

                    insert_date = {"stock_code": row['证券代码'], "stock_name": row['证券名称'], "type": '证券' + row['操作'],
                                   "num": row['成交数量'],
                                   "price": row['成交价格'],
                                   "commission": row['手续费'], "other_fee": 0,
                                   "amount_changed": -round(round(row['成交数量'] * row['成交价格'], 2) + row['手续费'], 2),
                                   "remained_amount": row['资金余额'], "trans_date": row['日期'],
                                   "flows_sequence": next_sequence, "trans_sequence": row['成交序号'], "status": 1}
                    strategy_flows.insert_strategy_flow(self.connection, insert_date)
            # for 循环结束以后，还要 commit 一次，要不然最后一天的没法提交了
            self.check_result()
            self.connection.commit()

        except Exception as e:
            # 任意失败则全部回滚
            self.connection.rollback()
            print(f"事务回滚，原因: {e}")
        finally:
            self.connection.close()

    def check_common_transaction(self, num, price, commission, trans_amount, remain_amount):
        print(f"available_balance: {self.available_balance}")
        if math.fabs(round(num * price, 2)) != trans_amount:
            print(f"{round(num * price, 2)}, {trans_amount}")
            exit("交易金额校验不通过")
        changed_amount = round(round(num * price, 2) + commission, 2)
        expected_remain_amount = round(self.available_balance - changed_amount, 2)
        if expected_remain_amount != remain_amount:
            print(f"{changed_amount}, {round(self.available_balance - changed_amount, 2)}, {remain_amount}")
            exit("交易后余额校验不通过")
        else:
            # 更新新的余额
            self.available_balance = round(self.available_balance - changed_amount, 2)


    def check_redemption(self, incomplete_flow, row):
        if incomplete_flow is None:
            print(row)
            exit("出错了, incomplete_flow 不存在")
        num = int(row['成交金额'] / row['成交价格'])
        if math.fabs(math.fabs(incomplete_flow['num']) - math.fabs(num)) > 5:
            print(row)
            print(incomplete_flow)
            exit("出错了，误差略大")
        other_fee = round(round(self.available_balance + row['成交金额'], 2) - row['资金余额'], 2)
        if other_fee / row['成交金额'] * 100 > 1:
            print(row)
            print(incomplete_flow)
            exit("other_fee过大，需要人工介入核实")
        self.check_common_transaction(incomplete_flow['num'], row['成交价格'], other_fee, row['成交金额'], row['资金余额'])
        return other_fee