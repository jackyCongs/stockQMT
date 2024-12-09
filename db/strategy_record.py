# coding=utf-8\
import time


def add(db,order_id,strategy_name,stock_code, bid_price, bid_num, target_start, remark):
    # 连接到数据库
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        update = ("insert into strategy_records(`order_id`, `strategy_name`,`stock_code`,`start_date`,`start_price`,"
                  "`num`,`start_target`,`created_at`,`remark`) "
                  "values "
                  "(%s, %s,%s, CURDATE(),%s, %s, %s, %s, %s)")

        cursor.execute(update, (order_id,strategy_name,stock_code,bid_price,bid_num,target_start, round(time.time()), remark))
        conn.commit()
    except Exception as e:
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()


def save(db, order_id, map):
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        updates = ', '.join([f"{key} = %s" for key in map.keys()])
        update = f"UPDATE strategy_records SET {updates} WHERE order_id = %s"
        values = list(map.values()) + [order_id]
        cursor.execute(update, values)
        conn.commit()
    except Exception as e:
        print(e)
    finally:
        cursor.close()
        conn.close()


def find(db, order_id):
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        # 构建查询语句，选择所有字段
        query = "SELECT * FROM strategy_records WHERE order_id = %s"
        # 执行查询
        cursor.execute(query, (order_id,))

        # 获取查询结果
        result = cursor.fetchone()
        if result:
            # 将结果转换为字典，字段名称作为键
            column_names = [desc[0] for desc in cursor.description]
            result_dict = dict(zip(column_names, result))
            return result_dict
        else:
            return None
    except Exception as e:
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()


def find_last_by_code(db, stock_code):
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        # 构建查询语句，选择所有字段
        query = "SELECT * FROM strategy_records WHERE stock_code = %s order by id desc limit 1"
        # 执行查询
        cursor.execute(query, (stock_code,))

        # 获取查询结果
        result = cursor.fetchone()
        if result:
            # 将结果转换为字典，字段名称作为键
            column_names = [desc[0] for desc in cursor.description]
            result_dict = dict(zip(column_names, result))
            return result_dict
        else:
            return None
    except Exception as e:
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()