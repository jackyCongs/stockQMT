# coding=utf-8
import pymysql
from pymysql import Error
import math
import pandas as pd

db_config = {
    'host': '47.104.167.147',
    'user': 'root',
    'password': '211314ok',
    'database': 'bill',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

ALLOWED_FIELDS = [
    'stock_code', 'stock_name', 'type', 'num', 'price', 'commission',
    'other_fee', 'amount_changed', 'remained_amount', 'trans_date', 'trans_sequence', 'flows_sequence', 'status'
]


def insert_strategy_flow(connection, data):
    """
    动态插入数据（支持部分字段）
    :param data: 包含字段的字典（至少一个有效字段）
    :return: 插入后的自增ID
    """
    # 过滤非法字段
    valid_data = {k: v for k, v in data.items() if k in ALLOWED_FIELDS}
    if not valid_data:
        raise ValueError("无有效字段可插入")

    # 构建动态SQL
    fields = valid_data.keys()
    columns = ', '.join([f'`{f}`' for f in fields])
    placeholders = ', '.join(['%s'] * len(fields))
    sql = f"INSERT INTO strategy_flows ({columns}) VALUES ({placeholders})"
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, list(valid_data.values()))
        return cursor.lastrowid
    except Error as e:
        print(f"插入失败: {e}")
        return None


def get_incomplete_flow_by_stock_code(connection, stock_code):
    try:
        with connection.cursor() as cursor:
            sql = f"SELECT * FROM strategy_flows WHERE stock_code = %s and `status` = 0 order by id asc limit 1"
            cursor.execute(sql, (stock_code,))
            return cursor.fetchone()
    except Error as e:
        print(f"数据库查询失败: {e}")
        return None


def get_by_max_flows_sequence(connection):
    try:
        with connection.cursor() as cursor:
            sql = f"SELECT * FROM strategy_flows order by flows_sequence desc limit 1"
            cursor.execute(sql)
            result = cursor.fetchone()
            return result
    except Error as e:
        print(f"数据库查询失败: {e}")
        return None


def get_max_flows_trans_date(connection):
    try:
        with connection.cursor() as cursor:
            sql = f"SELECT max(`trans_date`) as max_flows_date FROM strategy_flows"
            cursor.execute(sql)
            result = cursor.fetchone()
            return result['max_flows_date'] if result else None
    except Error as e:
        print(f"数据库查询失败: {e}")
        return None


def get_flow_by_trans_sequence(connection, trans_sequence):
    try:
        with connection.cursor() as cursor:
            sql = f"SELECT * FROM strategy_flows WHERE trans_sequence = %s"
            cursor.execute(sql, (trans_sequence,))
            return cursor.fetchone()
    except Error as e:
        print(f"数据库查询失败: {e}")
        return None


def update_strategy_flow(connection, data, id_value):
    """
    根据ID更新数据（支持更新多个字段）
    :param data: 包含更新字段的字典
    :param id_value: 要更新的记录ID
    :return: 影响的行数
    """
    # 过滤非法字段
    valid_data = {k: v for k, v in data.items() if k in ALLOWED_FIELDS}
    if not valid_data:
        raise ValueError("无有效字段可更新")

    # 构建动态SET子句
    set_clause = ', '.join([f'`{k}` = %s' for k in valid_data])
    sql = f"UPDATE strategy_flows SET {set_clause} WHERE id = %s"
    try:
        with connection.cursor() as cursor:
            # 参数顺序：更新字段的值 + ID值
            affected_rows = cursor.execute(sql, list(valid_data.values()) + [id_value])
        return affected_rows
    except Error as e:
        print(f"更新失败: {e}")
        return None


available_balance = 0


def check_common_transaction(num, price, commission, trans_amount, remain_amount):
    global available_balance
    print(f"available_balance: {available_balance}")
    if math.fabs(round(num * price, 2)) != trans_amount:
        print(f"{round(num * price, 2)}, {trans_amount}")
        exit("交易金额校验不通过")
    changed_amount = round(round(num * price, 2) + commission, 2)
    expected_remain_amount = round(available_balance - changed_amount, 2)
    if expected_remain_amount != remain_amount:
        print(f"{changed_amount}, {round(available_balance - changed_amount, 2)}, {remain_amount}")
        exit("交易后余额校验不通过")
    else:
        # 更新新的余额
        available_balance = round(available_balance - changed_amount, 2)


def check_redemption(incomplete_flow, row):
    global available_balance
    if incomplete_flow is None:
        print(row)
        exit("出错了, incomplete_flow 不存在")
    num = int(row['成交金额'] / row['成交价格'])
    if math.fabs(math.fabs(incomplete_flow['num']) - math.fabs(num)) > 5:
        print(row)
        print(incomplete_flow)
        exit("出错了，误差略大")
    other_fee = round(round(available_balance + row['成交金额'], 2) - row['资金余额'], 2)
    if other_fee / row['成交金额'] * 100 > 1:
        print(row)
        print(incomplete_flow)
        exit("other_fee过大，需要人工介入核实")
    check_common_transaction(incomplete_flow['num'], row['成交价格'], other_fee, row['成交金额'], row['资金余额'])
    return other_fee


class Trans_flows:
    def run(self):
        connection = pymysql.connect(**db_config)
        # 关闭自动提交
        connection.autocommit(False)

        last_flows = get_by_max_flows_sequence(connection)
        last_trans_date = get_max_flows_trans_date(connection)
        if last_flows is None or last_trans_date is None:
            exit("初始化错误!")

        available_balance = float(last_flows['remained_amount'])
        last_update_date = int(last_trans_date)

        # 读取CSV文件（注意编码问题）
        columns = [
            "成功", "错误", "日期", "成交时间", "市场类型", "市场",
            "股票账号", "证券代码", "证券名称", "买卖", "操作", "成交数量",
            "成交价格", "成交金额", "资金余额", "股份余额", "成交序号",
            "手续费", "印花税", "其它杂费"
        ]
        df = pd.read_excel('./file/99065905_2_stkDelivery.xlsx', engine='openpyxl')  # 如果文件是.xlsx格式
        current_process_date = 0

        try:
            for i, (index, row) in enumerate(df.iterrows(), start=1):
                # 过滤掉已经更新过的日期的数据
                if row['日期'] <= last_update_date:
                    continue

                if row['日期'] != current_process_date:
                    if current_process_date != 0:
                        connection.commit()

                    current_process_date = row['日期']

                last_flows = get_by_max_flows_sequence(connection)
                next_sequence = last_flows['flows_sequence'] + 1

                # print(f"第{i}行: 证券代码={row['证券代码']}, 买卖={row['买卖']},操作={row['操作']}, 成交数量={row['成交数量']}, 成交价格={row['成交价格']}, 成交金额={row['成交金额']}, 资金余额={row['资金余额']}, 成交序号={row['成交序号']}, 证券名称={row['证券名称']}, 成交数量={row['成交数量']}, 手续费={row['手续费']}, 日期={row['日期']}, 成交时间={row['成交时间']}, 市场类型={row['市场类型']}")
                hour = 9
                if len(str(row['成交时间'])) == 6:
                    hour = int(str(row['成交时间'])[:2])
                # 赎回交易
                if hour >= 16:
                    # 要识别是开始赎回，还是赎回到账
                    if row['成交数量'] != 0:
                        # 开始赎回
                        if get_flow_by_trans_sequence(connection, row['成交序号']):
                            print(f"成交序号: {row['成交序号']} 已经存在，处理忽略")
                            continue
                        insert_strategy_flow(connection,
                                             {"stock_code": row['证券代码'], "stock_name": row['证券名称'], "type": "开放基金赎回",
                                              "num": -math.fabs(row['成交数量']), "trans_date": row['日期'],
                                              "trans_sequence": row['成交序号'], "status": 0})
                    else:
                        # 赎回到账
                        # trans_sequence 如果已经存在了就忽略
                        trans_sequence_flow = get_flow_by_trans_sequence(connection, row['成交序号'])
                        if trans_sequence_flow and trans_sequence_flow['status'] == 1:
                            print(f"成交序号: {row['成交序号']} 已经存在，处理忽略")
                            continue
                        incomplete_flow = get_incomplete_flow_by_stock_code(connection, row['证券代码'])
                        other_fee = check_redemption(incomplete_flow, row)
                        changed_amount = round(row['成交金额'] - other_fee, 2)
                        update_strategy_flow(connection,
                                             {"price": row['成交价格'], "commission": 0, "other_fee": other_fee,
                                              "amount_changed": changed_amount,
                                              "remained_amount": row['资金余额'], "flows_sequence": next_sequence,
                                              "trans_sequence": row['成交序号'], "status": 1}, incomplete_flow['id'])
                else:
                    # 正常交易
                    check_common_transaction(row['成交数量'], row['成交价格'], row['手续费'], row['成交金额'], row['资金余额'])

                    insert_date = {"stock_code": row['证券代码'], "stock_name": row['证券名称'], "type": '证券' + row['操作'],
                                   "num": row['成交数量'],
                                   "price": row['成交价格'],
                                   "commission": row['手续费'], "other_fee": 0,
                                   "amount_changed": -round(round(row['成交数量'] * row['成交价格'], 2) + row['手续费'], 2),
                                   "remained_amount": row['资金余额'], "trans_date": row['日期'],
                                   "flows_sequence": next_sequence, "trans_sequence": row['成交序号'], "status": 1}
                    insert_strategy_flow(connection, insert_date)
            # for 循环结束以后，还要 commit 一次，要不然最后一天的没法提交了
            connection.commit()

        except Exception as e:
            # 任意失败则全部回滚
            connection.rollback()
            print(f"事务回滚，原因: {e}")
        finally:
            connection.close()