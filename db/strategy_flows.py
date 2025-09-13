# coding=utf-8
from pymysql import Error

ALLOWED_FIELDS = [
    'stock_code', 'stock_name', 'type', 'num', 'price', 'commission',
    'other_fee', 'amount_changed', 'remained_amount', 'trans_date', 'trans_sequence', 'flows_sequence', 'status'
]

def insert_strategy_flow(connection, data):
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



def get_flow_by_trans_sequence(connection, trans_sequence, stock_code):
    try:
        with connection.cursor() as cursor:
            sql = """
                SELECT * FROM strategy_flows
                WHERE trans_sequence = %s
                  AND stock_code = %s
                  AND trans_date >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 15 DAY), '%%Y%%m%%d')
            """
            cursor.execute(sql, (trans_sequence, str(stock_code)))
            return cursor.fetchone()
    except Error as e:
        print(f"数据库查询失败: {e}")
        return None


def update_strategy_flow(connection, data, id_value):
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