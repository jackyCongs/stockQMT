# coding=utf-8
import time
def get_stock_list(db, inner_etf_type):
    # 连接到数据库
    conn = db.get_connection()
    cursor = conn.cursor()
    row_dict_list = []
    try:
        if inner_etf_type == 'etf':
            query = "SELECT * FROM stock where is_etf = 1 and inner_etf_type = %s and status = 1"
        else:
            query = "SELECT * FROM stock where is_etf = 1 and inner_etf_type = %s and target_worth_url != '' and status = 1"
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


def get_stock_batch(db, last_id=0, limit=500):
    """
    通过锚点 ID 分批获取股票数据
    """
    conn = db.get_connection()
    cursor = conn.cursor()
    row_dict_list = []
    try:
        # 核心逻辑：使用 ID 过滤并排序，配合 LIMIT 控制步长
        query = """
            SELECT * FROM stock 
            WHERE id > %s and source = 'A'
            ORDER BY id ASC 
            LIMIT %s
        """
        cursor.execute(query, (last_id, limit))
        rows = cursor.fetchall()

        # 获取列名并转为字典格式
        column_names = [description[0] for description in cursor.description]
        for row in rows:
            row_dict_list.append(dict(zip(column_names, row)))

        return row_dict_list
    except Exception as e:
        print(f"查询出错: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


def get_unique_index_codes(db):
    """
    查询所有需要追踪的、去重的目标指数代码 (target_worth_url)
    条件：status=1 (正常), is_etf=1 (是ETF), 且 target_worth_url 非空

    :param db: 数据库连接管理对象 (假设包含 get_connection 方法)
    :return: list[str], 包含所有去重后的指数代码列表。如果出错返回 None。
    """
    conn = db.get_connection()
    cursor = conn.cursor()

    try:
        # 核心逻辑：使用 DISTINCT 去重，并加上 IS NOT NULL 和 != '' 过滤空值
        query = """
            SELECT DISTINCT target_worth_url 
            FROM stock 
            WHERE status = 1 
              AND is_etf = 1 
              AND target_worth_url IS NOT NULL 
              AND target_worth_url != ''
              and target_worth_url not like 'H%'
        """
        cursor.execute(query)
        rows = cursor.fetchall()

        # rows 是一个包含 tuple 的 list，例如: [('931151',), ('399006',), ('399673',)]
        # 我们通过列表推导式，将其扁平化为一个纯字符串列表
        index_codes = [row[0] for row in rows]

        # 记录日志 (可选)
        print(f"成功获取需要追踪的指数代码，共 {len(index_codes)} 个去重标的。")

        return index_codes

    except Exception as e:
        print(f"查询指数代码出错: {e}")
        return None
    finally:
        # 确保游标和连接被正确关闭
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