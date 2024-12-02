# coding=utf-8

def insert(db,order_id,strategy_name,stock_code, bid_price, bid_num, target_start):
    # 连接到数据库
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        update = ("insert into strategy_records(`order_id`, `strategy_name`,`stock_code`,`start_date`,`start_price`,`num`,`start_target`) "
                  "values "
                  "(%d, %s,%s, CURDATE(),%s, %d, %s)")

        cursor.execute(update, (order_id,strategy_name,stock_code,bid_price,bid_num,target_start))
        conn.commit()
    except Exception as e:
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()