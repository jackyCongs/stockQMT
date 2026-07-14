import pymysql
from pymysql import Error
import datetime


def upsert_index_tick(db, index_code: str, tick_data: dict):
    """
    Process tick updates for indices; insert or update daily history records.

    :param db: Database connection manager
    :param index_code: str, Index ticker code (e.g., 'sh000001')
    :param tick_data: dict, Raw tick data dictionary
    """
    conn = db.get_connection()
    try:
        # 1. Parse timestamp (convert milliseconds to YYYY-MM-DD)
        # 1774249209000 -> 2026-03-24
        trade_date = datetime.datetime.fromtimestamp(tick_data['time'] / 1000.0).strftime('%Y-%m-%d')

        # 2. Extract market depth / tick quote data
        last_price = tick_data['lastPrice']
        pre_close = tick_data['lastClose']
        open_price = tick_data['open']
        high_price = tick_data['high']
        low_price = tick_data['low']
        volume = tick_data['volume']
        amount = tick_data['amount']

        # 3. Pre-calculate core risk control metric: today's volatility rate
        # Protection logic: prevent division-by-zero errors in case previous close price is 0
        if pre_close and pre_close > 0:
            # Keep 4 decimal places, e.g., -0.0234
            vol_rate = round((last_price - pre_close) / pre_close, 4)
        else:
            vol_rate = 0.0

        # 4. Build SQL statement (leveraging ON DUPLICATE KEY UPDATE for fast upserts)
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

        # 5. Bind parameters and execute
        params = (
            index_code, trade_date,
            last_price, pre_close, open_price, high_price, low_price,
            vol_rate, volume, amount
        )

        with conn.cursor() as cursor:
            cursor.execute(sql, params)

        # Commit transaction
        conn.commit()

    except Exception as e:
        # Recommended to replace with logger.error in production environments
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
            # Use executemany for high-speed batch inserts
            cursor.executemany(sql, new_data_list)
        db_conn.commit()
    except Exception as e:
        db_conn.rollback()
        exit(f"Failed to batch upsert index history data: {e}")

def get_updated_index_codes_by_date(db, trade_date, record_type=None):
    # 2. Query unique codes that already exist in the database for the given date
    conn = db.get_connection()
    actual_codes = set()
    try:
        with conn.cursor() as cursor:
            # Finetune this index_code query format depending on whether your database stores suffixes
            if record_type:
                sql = "SELECT index_code FROM index_daily_history WHERE trade_date = %s AND type = %s"
                cursor.execute(sql, (trade_date, record_type))
            else:
                sql = "SELECT index_code FROM index_daily_history WHERE trade_date = %s"
                cursor.execute(sql, (trade_date,))
            rows = cursor.fetchall()
            actual_codes = set([row[0] for row in rows])
    except Exception as e:
        exit(f"Validation query failed: {e}")
    finally:
        conn.close()
    return actual_codes

def get_3_days_history_list(db, trade_date):
    conn = db.get_connection()
    try:
        with conn.cursor() as cursor:
            # 1. Dynamically retrieve the last 3 active trading dates (ensuring sequence T-2, T-1, T0)
            date_sql = """
                SELECT DISTINCT trade_date FROM index_daily_history 
                WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT 3
            """
            cursor.execute(date_sql, (trade_date,))
            dates = [row[0] for row in cursor.fetchall()]
            if len(dates) == 0:
                print("No history trading days found, cannot calculate penalty rate.")
                return None
            dates.sort()
            # Ascending sort: [T-2, T-1, T0]
            # 2. Query index volatility amplitudes for all 3 days in a single call
            # Note: Ensure the IN clause reads dates in ascending order
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
        print(f"Failed to retrieve pre-requisite data for historical penalty calculation: {e}")
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
        print(f"Failed to retrieve pre-requisite data for historical penalty calculation: {e}")
        conn.rollback()
    finally:
        conn.close()


def get_index_penalty_rate(db, index_code, trade_date, index_type = 'index'):
    conn = db.get_connection()
    try:
        with conn.cursor() as cursor:
            sql = """
                SELECT penalty_rate 
                FROM index_daily_history 
                WHERE index_code = %s AND trade_date = %s and type = %s
            """
            cursor.execute(sql, (index_code, trade_date, index_type))
            row = cursor.fetchone()
            if row is not None:
                return float(row[0])
            else:
                # ==========================================
                # Trigger fallback mechanism and alert warning
                # ==========================================
                alert_msg = (
                    f"⚠️ [Risk Control Downgrade Warning] No historical penalty rate found for [{index_code}] on [{trade_date}]!\n"
                    f"-> Potential causes: Post-market batch processing was not executed last night, the index is newly added today, or third-party data is missing.\n"
                    f"-> System action: Defaulting to 0.0 fallback. The ticker will trade without historical excess penalty today!"
                )
                print(alert_msg)

                return 0.0

    except Exception as e:
        # Prevent database disconnection from crashing the main trading thread
        print(f"❌ Fatal exception occurred while querying penalty rate for [{index_code}]: {e}. Defaulting to 0.0!")
        return 0.0
    finally:
        conn.close()


def get_batch_index_penalty_rates(db, code_type_list, trade_date):
    if not code_type_list:
        return {}

    conn = db.get_connection()
    result = {}
    try:
        # 按 type 分组，减少 SQL 复杂度
        type_groups = {}
        for code, idx_type in code_type_list:
            type_groups.setdefault(idx_type, []).append(code)

        with conn.cursor() as cursor:
            for idx_type, codes in type_groups.items():
                format_strings = ','.join(['%s'] * len(codes))
                sql = f"""
                    SELECT index_code, penalty_rate 
                    FROM index_daily_history 
                    WHERE index_code IN ({format_strings}) AND trade_date = %s AND type = %s
                """
                params = tuple(codes) + (trade_date, idx_type)
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                for row in rows:
                    idx_code = row['index_code'] if isinstance(row, dict) else row[0]
                    penalty_val = row['penalty_rate'] if isinstance(row, dict) else row[1]
                    result[idx_code] = float(penalty_val) if penalty_val is not None else 0.0

        # Fallback missing codes to 0.0 and print warnings (maintaining original flow logic)
        for code, idx_type in code_type_list:
            if code not in result:
                alert_msg = (
                    f"⚠️ [Risk Control Downgrade Warning] No historical penalty rate found for [{code}] on [{trade_date}]!\n"
                    f"-> Potential causes: Post-market batch processing was not executed last night, the index is newly added today, or third-party data is missing.\n"
                    f"-> System action: Defaulting to 0.0 fallback. The ticker will trade without historical excess penalty today!"
                )
                print(alert_msg)
                result[code] = 0.0

    except Exception as e:
        print(f"❌ Fatal exception occurred during batch query of penalty rates: {e}. Defaulting missing values to 0.0!")
        for code, idx_type in code_type_list:
            if code not in result:
                result[code] = 0.0
    finally:
        conn.close()

    return result


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
                # [Core Bug Fix] Add cursor type compatibility support: works for both dictionary and tuple cursors!
                idx_code = row['index_code'] if isinstance(row, dict) else row[0]
                close_px = row['close_price'] if isinstance(row, dict) else row[1]

                pre_close_map[idx_code] = float(close_px)

        return pre_close_map
    except Exception as e:
        # Recommended to print the error details rather than exit(e) to keep the main runner process alive
        print(f"Failed to extract index previous close value: {e}")
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
        print(f"❌️ Failed to extract ETF target index previous close value: {e}")
        return {}
    finally:
        conn.close()