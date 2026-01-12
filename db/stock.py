# coding=utf-8
import time
def get_stock_list(db, inner_etf_type):
    # 连接到数据库
    conn = db.get_connection()
    cursor = conn.cursor()
    row_dict_list = []
    try:
        query = "SELECT * FROM stock where is_etf = 1 and inner_etf_type = %s and target_worth_url != ''"
        cursor.execute(query, (str(inner_etf_type),))
        rows = cursor.fetchall()
        column_names = [description[0] for description in cursor.description]
        for row in rows:
            row_dict_list.append(dict(zip(column_names, row)))
        return row_dict_list
    except Exception as e:
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()


def get_stock_by_code(db, code):
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        query = "SELECT * FROM stock where code = %s order by id desc"
        cursor.execute(query, (str(code),))
        row = cursor.fetchone()
        column_names = [description[0] for description in cursor.description]
        return dict(zip(column_names, row))
    except Exception as e:
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()

def update_stock_net_worth(db, net_worth, net_worth_date, id):
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        update = f"UPDATE stock SET net_worth = %s, last_net_worth_date = %s WHERE id = %s"
        cursor.execute(update, (net_worth, net_worth_date, id,))
        conn.commit()
    except Exception as e:
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()

def update_stock_price(db, money, code):
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        update = f"UPDATE stock SET money = %s, updated_at = CURRENT_TIMESTAMP WHERE `code` = %s limit 1"
        cursor.execute(update, (money, code,))
        conn.commit()
    except Exception as e:
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()


def batch_update_stock_price(db, data_list):
    if not data_list:
        return
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        when_clauses = []
        where_codes = []
        sql_args = []

        for code, money in data_list:
            when_clauses.append("WHEN %s THEN %s")
            sql_args.append(code)
            sql_args.append(money)
            where_codes.append(code)

        # 构建最终的 SQL 语句
        # 注意：不再需要 limit 1，因为我们要更新多行
        sql = f"""
            UPDATE stock 
            SET money = CASE code 
                {' '.join(when_clauses)} 
            END,
            updated_at = CURRENT_TIMESTAMP
            WHERE code IN ({','.join(['%s'] * len(where_codes))})
        """
        # 将 WHERE IN 的参数拼接到参数列表中
        sql_args.extend(where_codes)
        # 执行这一条超长的 SQL
        cursor.execute(sql, sql_args)
        conn.commit()

    except Exception as e:
        print(f"Batch update failed: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


def update(db, stock_name,stock_code,target_worth_name, remark):
    # 连接到数据库
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        update_sql = ("UPDATE stock SET "
                      "`name` = %s, "
                      "`target_worth_name` = %s, "
                      "`remark` = %s, "
                      "`updated_at` = CURDATE() "
                      "WHERE `code` = %s")
        # 打印一下当前的传参，方便调试
        params = (stock_name, target_worth_name, remark, stock_code)
        print(f"正在更新数据: {params}")
        # 执行更新
        cursor.execute(update_sql, params)
        conn.commit()
        print("更新成功")
    except Exception as e:
        print('123456')
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()