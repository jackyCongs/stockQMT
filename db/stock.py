# coding=utf-8

def get_stock_list(db):
    # 连接到数据库
    conn = db.get_connection()
    cursor = conn.cursor()
    row_dict_list = []
    try:
        query = "SELECT * FROM stock where is_etf = 1 and inner_etf_type = 'lof' and target_worth_url != '' order by id desc limit 3"
        #query = "SELECT * FROM stock where is_etf = 1 order by id desc"
        cursor.execute(query)
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
        query = "SELECT * FROM stock where code = %s and is_etf = 1 order by id desc"
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
