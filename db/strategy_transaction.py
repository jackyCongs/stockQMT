# coding=utf-8
import time

def add(db, trade):
    # 连接到数据库
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        sql = ("""
                    INSERT INTO strategy_transaction (
                        stock_code, order_type,
                        traded_id, traded_time, traded_price, traded_volume,
                        traded_amount, order_id, order_sysid,
                        strategy_name, order_remark, created_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, CURDATE()
                    )
                """)
        cursor.execute(sql, (trade.stock_code, trade.order_type,
            trade.traded_id, trade.traded_time, trade.traded_price, trade.traded_volume,
            trade.traded_amount, trade.order_id, trade.order_sysid,
            trade.strategy_name, trade.order_remark, ))
        conn.commit()
    except Exception as e:
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()