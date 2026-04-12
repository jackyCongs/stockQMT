# coding=utf-8

import pymysql
from db import strategy_flows, fund
from db.db_pool import DBPool
import math
import pandas as pd
import configparser
from decimal import Decimal, ROUND_HALF_UP

from helper import date_utils


def load_config():
    config = configparser.ConfigParser()
    config.read('config.ini', encoding='utf-8')
    if config['FLOWS']['file'] is None or config['FLOWS']['file'] == "":
        exit("文件名存在异常")
    return config


class TransFlows:
    def __init__(self, asset, platform):
        self.last_update_date = None
        self.db = DBPool()
        self.asset = asset
        self.available_balance = Decimal('0.0')
        self.platform = platform
        self.connection = self.db.get_connection()
        # 关闭自动提交，开启以天为单位的事务，要么全部成功要么全部失败
        self.connection.autocommit(False)
        self.config = load_config()
        self.reversed_purchase_balance = Decimal('0.0')

    def _to_decimal(self, value):
        """安全地将各种类型转换为 Decimal"""
        if pd.isna(value) or value == '':
            return Decimal('0.0')
        return Decimal(str(value))

    def _round_money(self, value):
        """执行标准的四舍五入，保留两位小数"""
        if not isinstance(value, Decimal):
            value = self._to_decimal(value)
        return value.quantize(Decimal('0.00'), rounding=ROUND_HALF_UP)

    def get_file(self):
        if self.platform is None:
            exit("platform未知，无法获取到对账文件")
        if self.platform == "大同证券":
            return self.config['FLOWS']['file']
        if self.platform == "湘财证券":
            return self.config['FLOWS']['xiangcai_file']
        exit("platform未知，无法获取到对账文件")

    def check_result(self):
        cash_dec = self._to_decimal(self.asset.cash)
        if self.reversed_purchase_balance > Decimal('0'):
            interest = cash_dec - self.available_balance - self.reversed_purchase_balance
            if (interest / self.reversed_purchase_balance * Decimal('100')) < Decimal('0.5') and cash_dec == (
                    self.available_balance + self.reversed_purchase_balance + interest):
                # 有效利息
                fund.insert_record(self.db, 2, float(self._round_money(interest)), self.platform,
                                   date_utils.format_date_fast(self.last_update_date), "逆回购利息", False)
                print(f"逆回购利息计算成功, 利息: {float(interest)}")
            else:
                print(
                    f"cash: {self.asset.cash}, available_balance: {float(self.available_balance)}, total: {float(self.available_balance + self.reversed_purchase_balance + interest)}")
                exit(
                    f"逆回购利息计算异常: 利息{float(interest)}元，买入逆回购金额: {float(self.reversed_purchase_balance)}")
        else:
            if self.available_balance != cash_dec:
                exit(
                    f"对账不通过，账本余额和实际余额不一致: accounting: {float(self.available_balance)}, cash: {self.asset.cash}")
        print(f"对账通过: 当前可用余额: {float(self.available_balance)}")

    def run(self):
        last_flows = strategy_flows.get_by_max_flows_sequence_by_platform(self.connection, self.platform)
        last_trans_date = strategy_flows.get_max_flows_trans_date(self.connection, self.platform)
        if last_flows is None or last_trans_date is None:
            exit("初始化错误!")
        self.available_balance = self._to_decimal(last_flows['remained_amount'])
        self.last_update_date = int(last_trans_date)
        df = pd.read_excel(self.get_file(), engine='openpyxl')
        if self.platform == "湘财证券":
            df = df.iloc[::-1].reset_index(drop=True)
        current_process_date = 0
        try:
            for i, (index, row) in enumerate(df.iterrows(), start=1):
                if self.platform == "湘财证券":
                    self.format_xiangcai_to_standard(row)
                # 过滤掉已经更新过的日期的数据
                if row['日期'] <= self.last_update_date:
                    continue
                if row['日期'] != current_process_date:
                    if current_process_date != 0:
                        self.connection.commit()
                    current_process_date = row['日期']

                last_flows = strategy_flows.get_by_max_flows_sequence(self.connection)
                next_sequence = last_flows['flows_sequence'] + 1

                hour = 9
                if len(str(row['成交时间'])) == 6:
                    hour = int(str(row['成交时间'])[:2])
                # 赎回交易
                if 16 <= hour < 18:
                    # 要识别是开始赎回，还是赎回到账
                    if row['成交金额'] == 0:
                        # 开始赎回
                        if row['成交数量'] == 0:
                            # 成交金额空，数量也空没有实际意义，直接忽略
                            continue
                        trans_sequence_flow = strategy_flows.get_flow_by_trans_sequence(self.connection, row['成交序号'], row['证券代码'], self.platform)
                        if trans_sequence_flow and trans_sequence_flow['status'] == 1:
                            exit(f"成交序号: {row['成交序号']} 已经存在，需要验证是否出现重复序号，如果唯一成交序号重复，需要手动修改一下前序号")
                        if trans_sequence_flow:
                            print(f"成交序号: {row['成交序号']} 已经存在，处理忽略")
                            continue
                        strategy_flows.insert_strategy_flow(self.connection,
                                                            {"stock_code": row['证券代码'],
                                                             "stock_name": row['证券名称'], "type": "开放基金赎回",
                                                             "num": -math.fabs(row['成交数量']),
                                                             "trans_date": row['日期'],
                                                             "trans_sequence": row['成交序号'], "status": 0,
                                                             "platform": self.platform})
                    else:
                        # 赎回到账
                        # trans_sequence 如果已经存在了就忽略
                        trans_sequence_flow = strategy_flows.get_flow_by_trans_sequence(self.connection, row['成交序号'], row['证券代码'], self.platform)
                        if trans_sequence_flow and trans_sequence_flow['status'] == 1:
                            exit(f"成交序号: {row['成交序号']} 已经存在，需要人工介入查看")
                        incomplete_flow = strategy_flows.get_incomplete_flow_by_stock_code(self.connection, row['成交序号'], row['证券代码'], self.platform)
                        other_fee_dec = self.check_redemption(incomplete_flow, row)
                        changed_amount = self._round_money(self._to_decimal(row['成交金额']) - other_fee_dec)
                        strategy_flows.update_strategy_flow(self.connection,
                                                            {"price": row['成交价格'], "commission": 0,
                                                             "other_fee": float(other_fee_dec),
                                                             "amount_changed": float(changed_amount),
                                                             "remained_amount": row['资金余额'],
                                                             "flows_sequence": next_sequence,
                                                             "trans_sequence": row['成交序号'], "status": 1},
                                                            incomplete_flow['id'])
                else:
                    # 过滤逆回购
                    if str(row['证券代码']) in ["131810", "204001"]:
                        trans_amount_dec = self._to_decimal(row['成交金额'])
                        commission_dec = self._to_decimal(row['手续费'])

                        if row['成交数量'] < 0:
                            self.available_balance = self._round_money(self.available_balance + trans_amount_dec)
                            print(float(self.available_balance))
                            continue
                        print("过滤逆回购")
                        self.available_balance = self._round_money(
                            self.available_balance - trans_amount_dec - commission_dec)
                        self.reversed_purchase_balance = self.reversed_purchase_balance + trans_amount_dec
                        print(float(self.available_balance))
                        continue
                    # 正常交易
                    self.check_common_transaction(row['成交数量'], row['成交价格'], row['手续费'], row['成交金额'], row['资金余额'])

                    num_dec = self._to_decimal(row['成交数量'])
                    price_dec = self._to_decimal(row['成交价格'])
                    commission_dec = self._to_decimal(row['手续费'])
                    amount_changed = -self._round_money(self._round_money(num_dec * price_dec) + commission_dec)
                    insert_date = {"stock_code": row['证券代码'], "stock_name": row['证券名称'],
                                   "type": '证券' + row['操作'],
                                   "num": row['成交数量'],
                                   "price": row['成交价格'],
                                   "commission": row['手续费'], "other_fee": 0,
                                   "amount_changed": float(amount_changed),
                                   "remained_amount": row['资金余额'], "trans_date": row['日期'],
                                   "platform": self.platform,
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
        num_dec = self._to_decimal(num)
        price_dec = self._to_decimal(price)
        commission_dec = self._to_decimal(commission)
        trans_amount_dec = self._to_decimal(trans_amount)
        remain_amount_dec = self._to_decimal(remain_amount)

        # 严格遵守原逻辑：保留原始的正负号
        calculated_trans = self._round_money(num_dec * price_dec)

        # 原逻辑：只在比对“成交金额”时使用绝对值判断
        if abs(calculated_trans) != trans_amount_dec:
            print(f"计算成交金额: {float(calculated_trans)}, 实际成交金额: {float(trans_amount_dec)}")
            exit("交易金额校验不通过")

        # 修正：去掉了之前擅自加的 abs()，原汁原味还原 round(round(num * price, 2) + commission, 2)
        changed_amount = self._round_money(calculated_trans + commission_dec)
        expected_remain_amount = self._round_money(self.available_balance - changed_amount)

        if expected_remain_amount != remain_amount_dec:
            print(
                f"变动金额: {float(changed_amount)}, 预期余额: {float(expected_remain_amount)}, 实际余额: {float(remain_amount_dec)}")
            exit("交易后余额校验不通过")
        else:
            # 更新新的余额
            self.available_balance = expected_remain_amount
        print(f"available_balance: {float(self.available_balance)}")

    def check_redemption(self, incomplete_flow, row):
        if incomplete_flow is None:
            print(row)
            exit("出错了, incomplete_flow 不存在")

        trans_amount_dec = self._to_decimal(row['成交金额'])
        price_dec = self._to_decimal(row['成交价格'])
        remain_amount_dec = self._to_decimal(row['资金余额'])

        num = int(trans_amount_dec / price_dec) if price_dec != Decimal('0') else 0
        if math.fabs(math.fabs(incomplete_flow['num']) - math.fabs(num)) > 5:
            print(row)
            print(incomplete_flow)
            exit("出错了，误差略大")

        other_fee = self._round_money(self._round_money(self.available_balance + trans_amount_dec) - remain_amount_dec)

        if trans_amount_dec != Decimal('0') and abs(other_fee / trans_amount_dec * Decimal('100')) > Decimal('1'):
            print(row)
            print(incomplete_flow)
            exit(
                f"other_fee过大: {float(other_fee)}, 占比: {float(abs(other_fee / trans_amount_dec * Decimal('100')))}%，需要人工介入核实")
        if other_fee < Decimal('0'):
            print(row)
            print(incomplete_flow)
            exit(
                f"other_fee异常: {float(other_fee)}, 占比: {float(abs(other_fee / trans_amount_dec * Decimal('100')) if trans_amount_dec != Decimal('0') else 0)}%，需要人工介入核实")

        self.check_common_transaction(incomplete_flow['num'], row['成交价格'], float(other_fee), row['成交金额'],
                                      row['资金余额'])
        return other_fee

    def format_xiangcai_to_standard(self, row):
        # 1. 直接映射 (Key对Key)
        row['日期'] = row['发生日期']
        row['股票账号'] = row['股东代码']
        row['证券代码'] = row['证券代码']
        row['证券名称'] = row['证券名称']
        row['成交数量'] = row['成交数量']
        row['成交价格'] = row['成交价格']
        row['成交金额'] = row['成交金额']
        row['发生金额'] = row['发生金额']
        row['资金余额'] = row['剩余金额']
        row['股份余额'] = row['剩余数量']
        row['成交序号'] = row['成交编号']
        row['手续费'] = row['手续费'] + row['印花税'] + row['过户费']
        row['印花税'] = row['印花税']
        row['其它杂费'] = 0
        # 2. 格式化处理：成交时间 (标准格式通常不带冒号)
        # 湘财是 "10:04:25"，标准是 "100425"
        if isinstance(row['成交时间'], str) and ':' in row['成交时间']:
            row['成交时间'] = row['成交时间'].replace(':', '')
        row['操作'] = row['买卖标志']
        if '买入' in row['买卖标志']:
            row['买卖'] = 1
        elif '卖出' in row['买卖标志']:
            row['买卖'] = 2
        else:
            row['买卖'] = 0  # 其他业务逻辑
        # 4. 补全标准格式特有但湘财缺失的字段
        # 根据图2，市场类型通常为 1，市场为空白
        row['市场类型'] = 1
        row['市场'] = ""
        return row