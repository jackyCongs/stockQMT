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
        exit("Invalid configuration: missing or corrupt filename")
    return config


class TransFlows:
    def __init__(self, asset, platform):
        self.last_update_date = None
        self.db = DBPool()
        self.asset = asset
        self.available_balance = Decimal('0.0')
        self.platform = platform
        self.connection = self.db.get_connection()
        # Disable autocommit, enable daily-based transactions (all-or-nothing execution)
        self.connection.autocommit(False)
        self.config = load_config()
        self.reversed_purchase_balance = Decimal('0.0')

    def _to_decimal(self, value):
        """Safely convert various types to Decimal"""
        if pd.isna(value) or value == '':
            return Decimal('0.0')
        return Decimal(str(value))

    def _round_money(self, value):
        """Perform standard rounding, retaining two decimal places"""
        if not isinstance(value, Decimal):
            value = self._to_decimal(value)
        return value.quantize(Decimal('0.00'), rounding=ROUND_HALF_UP)

    def get_file(self):
        if self.platform is None:
            exit("Unknown platform: cannot retrieve reconciliation file.")
        if self.platform == "大同证券":
            return self.config['FLOWS']['file']
        if self.platform == "湘财证券":
            return self.config['FLOWS']['xiangcai_file']
        exit("Unknown platform: cannot retrieve reconciliation file.")

    def check_result(self):
        cash_dec = self._to_decimal(self.asset.cash)
        if self.reversed_purchase_balance > Decimal('0'):
            interest = cash_dec - self.available_balance - self.reversed_purchase_balance
            if (interest / self.reversed_purchase_balance * Decimal('100')) < Decimal('0.5') and cash_dec == (
                    self.available_balance + self.reversed_purchase_balance + interest):
                # Valid interest
                fund.insert_record(self.db, 2, float(self._round_money(interest)), self.platform,
                                   date_utils.format_date_fast(self.last_update_date), "逆回购利息", False)
                print(f"Reverse repo interest computed successfully. Interest: {float(interest)}")
            else:
                print(
                    f"cash: {self.asset.cash}, available_balance: {float(self.available_balance)}, total: {float(self.available_balance + self.reversed_purchase_balance + interest)}")
                exit(
                    f"Reverse repo interest calculation anomaly: Interest {float(interest)} CNY, Reverse repo purchase amount: {float(self.reversed_purchase_balance)}")
        else:
            if self.available_balance != cash_dec:
                exit(
                    f"Reconciliation failed. Ledger balance does not match actual balance: accounting: {float(self.available_balance)}, cash: {self.asset.cash}")
        print(f"Reconciliation passed. Current available balance: {float(self.available_balance)}")

    def run(self):
        last_flows = strategy_flows.get_by_max_flows_sequence_by_platform(self.connection, self.platform)
        last_trans_date = strategy_flows.get_max_flows_trans_date(self.connection, self.platform)
        if last_flows is None or last_trans_date is None:
            exit("Initialization error!")
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
                # Filter out records with dates already updated
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
                # Redemption transaction
                if 16 <= hour < 18:
                    # Distinguish between initiation of redemption and fund arrival
                    if row['成交金额'] == 0:
                        # Initiate redemption
                        if row['成交数量'] == 0:
                            # Ignore if both volume and amount are empty
                            continue
                        trans_sequence_flow = strategy_flows.get_flow_by_trans_sequence(self.connection, row['成交序号'], row['证券代码'], self.platform)
                        if trans_sequence_flow and trans_sequence_flow['status'] == 1:
                            exit(f"Execution index: {row['成交序号']} already exists. Please verify index uniqueness; manual correction required if duplicated.")
                        if trans_sequence_flow:
                            print(f"Execution index: {row['成交序号']} already exists. Skipping record.")
                            continue
                        strategy_flows.insert_strategy_flow(self.connection,
                                                            {"stock_code": row['证券代码'],
                                                             "stock_name": row['证券名称'], "type": "开放基金赎回",
                                                             "num": -math.fabs(row['成交数量']),
                                                             "trans_date": row['日期'],
                                                             "trans_sequence": row['成交序号'], "status": 0,
                                                             "platform": self.platform})
                    else:
                        # Redemption cash received
                        # Skip if trans_sequence already exists
                        trans_sequence_flow = strategy_flows.get_flow_by_trans_sequence(self.connection, row['成交序号'], row['证券代码'], self.platform)
                        if trans_sequence_flow and trans_sequence_flow['status'] == 1:
                            exit(f"Execution index: {row['成交序号']} already exists. Manual intervention required.")
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
                    # Filter reverse repo
                    if str(row['证券代码']) in ["131810", "204001"]:
                        trans_amount_dec = self._to_decimal(row['成交金额'])
                        commission_dec = self._to_decimal(row['手续费'])

                        if row['成交数量'] < 0:
                            self.available_balance = self._round_money(self.available_balance + trans_amount_dec)
                            print(float(self.available_balance))
                            continue
                        print("Filtered reverse repo")
                        self.available_balance = self._round_money(
                            self.available_balance - trans_amount_dec - commission_dec)
                        self.reversed_purchase_balance = self.reversed_purchase_balance + trans_amount_dec
                        print(float(self.available_balance))
                        continue
                    # Normal transaction
                    commission_dec = self._to_decimal(row['手续费'] + row['其它杂费'] + row['印花税'])
                    self.check_common_transaction(row['成交数量'], row['成交价格'], commission_dec, row['成交金额'], row['资金余额'])

                    num_dec = self._to_decimal(row['成交数量'])
                    price_dec = self._to_decimal(row['成交价格'])
                    amount_changed = -self._round_money(self._round_money(num_dec * price_dec) + commission_dec)
                    insert_date = {"stock_code": row['证券代码'], "stock_name": row['证券名称'],
                                   "type": '证券' + row['操作'],
                                   "num": row['成交数量'],
                                   "price": row['成交价格'],
                                   "commission": commission_dec, "other_fee": 0,
                                   "amount_changed": float(amount_changed),
                                   "remained_amount": row['资金余额'], "trans_date": row['日期'],
                                   "platform": self.platform,
                                   "flows_sequence": next_sequence, "trans_sequence": row['成交序号'], "status": 1}
                    strategy_flows.insert_strategy_flow(self.connection, insert_date)
            # Execute a final commit after the loop to persist the last day's records
            self.check_result()
            self.connection.commit()

        except Exception as e:
            # Rollback transaction upon any failure
            self.connection.rollback()
            print(f"Transaction rolled back. Reason: {e}")
        finally:
            self.connection.close()

    def check_common_transaction(self, num, price, commission, trans_amount, remain_amount):
        num_dec = self._to_decimal(num)
        price_dec = self._to_decimal(price)
        commission_dec = self._to_decimal(commission)
        trans_amount_dec = self._to_decimal(trans_amount)
        remain_amount_dec = self._to_decimal(remain_amount)

        # Retain original sign to preserve native logic
        calculated_trans = self._round_money(num_dec * price_dec)

        # Absolute value evaluation is used only when matching transaction amounts
        if abs(calculated_trans) != trans_amount_dec:
            print(f"Calculated trade amount: {float(calculated_trans)}, actual amount: {float(trans_amount_dec)}")
            exit("Trade amount validation failed")

        # Reverted abs() modifications to preserve round(round(num * price, 2) + commission, 2)
        changed_amount = self._round_money(calculated_trans + commission_dec)
        expected_remain_amount = self._round_money(self.available_balance - changed_amount)

        if expected_remain_amount != remain_amount_dec:
            print(
                f"Changed amount: {float(changed_amount)}, expected balance: {float(expected_remain_amount)}, actual balance: {float(remain_amount_dec)}")
            exit("Post-trade balance validation failed")
        else:
            # Update to new balance
            self.available_balance = expected_remain_amount
        print(f"available_balance: {float(self.available_balance)}")

    def check_redemption(self, incomplete_flow, row):
        if incomplete_flow is None:
            print(row)
            exit("Error: incomplete_flow does not exist")

        trans_amount_dec = self._to_decimal(row['成交金额'])
        price_dec = self._to_decimal(row['成交价格'])
        remain_amount_dec = self._to_decimal(row['资金余额'])

        num = int(trans_amount_dec / price_dec) if price_dec != Decimal('0') else 0
        if math.fabs(math.fabs(incomplete_flow['num']) - math.fabs(num)) > 5:
            print(row)
            print(incomplete_flow)
            exit("Error: variance exceeds threshold")

        other_fee = self._round_money(self._round_money(self.available_balance + trans_amount_dec) - remain_amount_dec)

        if trans_amount_dec != Decimal('0') and abs(other_fee / trans_amount_dec * Decimal('100')) > Decimal('1'):
            print(row)
            print(incomplete_flow)
            exit(
                f"other_fee excessive: {float(other_fee)}, ratio: {float(abs(other_fee / trans_amount_dec * Decimal('100')))}%. Manual verification required.")
        if other_fee < Decimal('0'):
            print(row)
            print(incomplete_flow)
            exit(
                f"other_fee anomaly: {float(other_fee)}, ratio: {float(abs(other_fee / trans_amount_dec * Decimal('100')) if trans_amount_dec != Decimal('0') else 0)}%. Manual verification required.")

        self.check_common_transaction(incomplete_flow['num'], row['成交价格'], float(other_fee), row['成交金额'],
                                      row['资金余额'])
        return other_fee

    def format_xiangcai_to_standard(self, row):
        # 1. Direct mapping (key-to-key)
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
        # 2. Format transaction time (standard format does not contain colons)
        # Xiangcai uses "10:04:25", standard uses "100425"
        if isinstance(row['成交时间'], str) and ':' in row['成交时间']:
            row['成交时间'] = row['成交时间'].replace(':', '')
        row['操作'] = row['买卖标志']
        if '买入' in row['买卖标志']:
            row['买卖'] = 1
        elif '卖出' in row['买卖标志']:
            row['买卖'] = 2
        else:
            row['买卖'] = 0  # 其他业务逻辑
        # 4. Complete fields unique to standard format but missing in Xiangcai
        # Market type defaults to 1, market field is empty
        row['市场类型'] = 1
        row['市场'] = ""
        return row