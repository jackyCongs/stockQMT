import pymysql
from pymysql import Error
import datetime


def upsert_index_tick(db, index_code: str, tick_data: dict):
    """
    处理指数的 Tick 推送，插入或更新当日日线数据。

    :param conn: pymysql connection object (数据库连接对象)
    :param index_code: str, 指数代码 (例如 'sh000001')
    :param tick_data: dict, 推送的原始字典数据
    """
    conn = db.get_connection()
    try:
        # 1. 解析时间戳 (毫秒级转为 YYYY-MM-DD)
        # 1774249209000 -> 2026-03-24
        trade_date = datetime.datetime.fromtimestamp(tick_data['time'] / 1000.0).strftime('%Y-%m-%d')

        # 2. 提取盘口数据
        last_price = tick_data['lastPrice']
        pre_close = tick_data['lastClose']
        open_price = tick_data['open']
        high_price = tick_data['high']
        low_price = tick_data['low']
        volume = tick_data['volume']
        amount = tick_data['amount']

        # 3. 预计算核心风控参数：当日真实涨跌幅 (Volatility Rate)
        # 保护逻辑：防止极小概率的昨收价为0导致除零报错
        if pre_close and pre_close > 0:
            # 保留4位小数，例如 -0.0234
            vol_rate = round((last_price - pre_close) / pre_close, 4)
        else:
            vol_rate = 0.0

        # 4. 构建 SQL 语句 (利用 ON DUPLICATE KEY UPDATE 实现极速覆写)
        sql = """
            INSERT INTO index_daily_history 
            (index_code, trade_date, close_price, pre_close, open_price, high_price, low_price, volatility_rate, volume, amount)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
            close_price = VALUES(close_price),
            high_price = VALUES(high_price),
            low_price = VALUES(low_price),
            volatility_rate = VALUES(volatility_rate),
            volume = VALUES(volume),
            amount = VALUES(amount),
            update_time = CURRENT_TIMESTAMP
        """

        # 5. 绑定参数并执行
        params = (
            index_code, trade_date,
            last_price, pre_close, open_price, high_price, low_price,
            vol_rate, volume, amount
        )

        with conn.cursor() as cursor:
            cursor.execute(sql, params)

        # 提交事务
        conn.commit()

    except Exception as e:
        # 生产环境建议替换为 logger.error
        print(f"Error upserting tick for {index_code}: {e}")
        conn.rollback()


def batch_upsert_index_history(db, update_data_list, record_type='index'):
    db_conn = db.get_connection()
    if not update_data_list:
        return
    
    new_data_list = [tuple(list(row) + [record_type]) for row in update_data_list]
    sql = """
            INSERT INTO index_daily_history 
            (index_code, trade_date, close_price, pre_close, open_price, high_price, low_price, volatility_rate, volume, amount, data_source, type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
            close_price = VALUES(close_price),
            pre_close = VALUES(pre_close),
            open_price = VALUES(open_price),
            high_price = VALUES(high_price),
            low_price = VALUES(low_price),
            volatility_rate = VALUES(volatility_rate),
            volume = VALUES(volume),
            amount = VALUES(amount),
            data_source = VALUES(data_source),
            type = VALUES(type),
            update_time = CURRENT_TIMESTAMP
        """
    try:
        with db_conn.cursor() as cursor:
            # 使用 executemany 进行极速批量写入
            cursor.executemany(sql, new_data_list)
        db_conn.commit()
    except Exception as e:
        db_conn.rollback()
        exit(f"批量 Upsert 指数历史数据失败: {e}")

def get_updated_index_codes_by_date(db, trade_date, record_type=None):
# 2. 从数据库查询该日期已存在的去重代码
    conn = db.get_connection()
    actual_codes = set()
    try:
        with conn.cursor() as cursor:
            # 这里的 index_code 建议根据你数据库存的是带后缀还是不带后缀来微调
            if record_type:
                sql = "SELECT index_code FROM index_daily_history WHERE trade_date = %s AND type = %s"
                cursor.execute(sql, (trade_date, record_type))
            else:
                sql = "SELECT index_code FROM index_daily_history WHERE trade_date = %s"
                cursor.execute(sql, (trade_date,))
            rows = cursor.fetchall()
            actual_codes = set([row[0] for row in rows])
    except Exception as e:
        exit(f"校验查询失败: {e}")
    finally:
        conn.close()
    return actual_codes

def get_3_days_history_list(db, trade_date):
    conn = db.get_connection()
    try:
        with conn.cursor() as cursor:
            # 1. 动态获取最近的 3 个有效交易日 (保证顺序是 T-2, T-1, T0)
            date_sql = """
                SELECT DISTINCT trade_date FROM index_daily_history 
                WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT 3
            """
            cursor.execute(date_sql, (trade_date,))
            dates = [row[0] for row in cursor.fetchall()]
            if len(dates) < 3:
                print("历史交易日不足3天，无法计算惩罚值")
                return None
            dates.sort()
            # 升序排列：[T-2, T-1, T0]
            # 2. 一次性查出这 3 天的所有指数波动率
            # 注意：需确保 IN 语句按升序日期读取
            format_strings = ','.join(['%s'] * len(dates))
            data_sql = f"""
                SELECT index_code, trade_date, 
                IF(pre_close > 0, 
                   (GREATEST(high_price, pre_close) - LEAST(low_price, pre_close)) / pre_close, 
                   0) AS true_amplitude 
            FROM index_daily_history 
            WHERE trade_date IN ({format_strings})
            ORDER BY trade_date ASC
            """
            cursor.execute(data_sql, tuple(dates))
            return cursor.fetchall()
    except Exception as e:
        print(f"获取计算历史惩罚值前置数据失败: {e}")
        conn.rollback()
    finally:
        conn.close()
def update_penalty_data(db, update_penalty_data):
    conn = db.get_connection()
    try:
        update_sql = """
                            UPDATE index_daily_history 
                            SET penalty_rate = %s 
                            WHERE index_code = %s AND trade_date = %s
                        """
        with conn.cursor() as cursor:
            cursor.executemany(update_sql, update_penalty_data)
        conn.commit()
    except Exception as e:
        print(f"获取计算历史惩罚值前置数据失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def get_index_penalty_rate(db, index_code, trade_date):
    conn = db.get_connection()
    try:
        with conn.cursor() as cursor:
            sql = """
                SELECT penalty_rate 
                FROM index_daily_history 
                WHERE index_code = %s AND trade_date = %s
            """
            cursor.execute(sql, (index_code, trade_date))
            row = cursor.fetchone()
            if row is not None:
                return float(row[0])
            else:
                # ==========================================
                # 触发兜底机制与强提醒
                # ==========================================
                alert_msg = (
                    f"⚠️ 【风控降级警告】未找到 [{index_code}] 在 [{trade_date}] 的历史惩罚值！\n"
                    f"-> 原因可能为：昨晚盘后批处理未执行、该指数为今日新增、或第三方数据完全丢失。\n"
                    f"-> 系统动作：已强行按 0.0 兜底返回，该标的今日将不带历史超额惩罚参与交易！"
                )
                print(alert_msg)
                # print(alert_msg) # 如果你习惯看控制台，也可以把 print 打开

                return 0.0

    except Exception as e:
        # 防止数据库突然断连导致盘中主线程崩溃
        print(f"❌ 查询 [{index_code}] 惩罚值时发生致命异常: {e}，强行按 0.0 兜底！")
        return 0.0
    finally:
        conn.close()


def get_index_pre_close(db_pool, trade_date):
    conn = db_pool.get_connection()
    if not conn:
        return {}

    pre_close_map = {}
    try:
        with conn.cursor() as cursor:
            sql = """
                SELECT index_code, close_price 
                FROM index_daily_history 
                WHERE trade_date = %s
            """
            cursor.execute(sql, (trade_date,))
            rows = cursor.fetchall()

            for row in rows:
                # 【核心修复】加入游标类型兼容装甲：无论是字典游标还是元组游标，统统拿下！
                idx_code = row['index_code'] if isinstance(row, dict) else row[0]
                close_px = row['close_price'] if isinstance(row, dict) else row[1]

                pre_close_map[idx_code] = float(close_px)

        return pre_close_map
    except Exception as e:
        # 这里建议用 print 打印具体错误，而不是直接 exit(e) 把整个主程序杀掉
        print(f"提取指数昨收点位失败: {e}")
        return {}
    finally:
        conn.close()

def get_etf_target_index_pre_close(db_pool, etf_codes: list, trade_date: str):
    conn = db_pool.get_connection()
    if not conn or not etf_codes:
        return {}

    result_map = {}
    try:
        with conn.cursor() as cursor:
            format_strings = ','.join(['%s'] * len(etf_codes))
            sql = f"""
                select s.name as etf_name, s.code as etf_code, i.index_code, i.close_price as index_pre_price 
                from stock as s 
                left join index_daily_history as i on s.target_worth_url = i.index_code 
                where s.code in ({format_strings}) and i.trade_date = %s
            """
            params = tuple(etf_codes) + (trade_date,)
            cursor.execute(sql, params)
            rows = cursor.fetchall()

            for row in rows:
                if isinstance(row, dict):
                    etf_code = row['etf_code']
                    index_code = row['index_code']
                    index_pre_price = row['index_pre_price']
                else:
                    etf_code = row[1]
                    index_code = row[2]
                    index_pre_price = row[3]

                result_map[str(etf_code)] = {
                    "index_code": index_code,
                    "index_pre_price": float(index_pre_price) if index_pre_price is not None else 0.0
                }

        return result_map
    except Exception as e:
        print(f"❌️ 提取 ETF 目标指数昨收点位失败: {e}")
        return {}
    finally:
        conn.close()