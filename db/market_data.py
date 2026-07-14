# coding=utf-8


def batch_insert_market_data(db, data_list):
    # Connect to the database
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        # Prepare SQL insert statement template
        insert_sql = """
            INSERT INTO market_data (`stock_code`, `time`, `open`, `high`, `low`, `close`, `volume`, `amount`, `settle`, `openInterest`, `preClose`, `suspendFlag`)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        # Execute batch insertion
        cursor.executemany(insert_sql, data_list)
        conn.commit()
    except Exception as e:
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()


def find_data(db, stock_code, time):
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        query = "select * from market_data where stock_code = %s and time = %s"
        cursor.execute(query, (str(stock_code), str(time),))
        row = cursor.fetchone()
        column_names = [description[0] for description in cursor.description]
        return dict(zip(column_names, row))
    except Exception as e:
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()


def find_next_data(db, stock_code, time):
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        query = "select * from market_data where stock_code = %s and time > %s order by time asc limit 1"
        cursor.execute(query, (str(stock_code), str(time),))
        row = cursor.fetchone()
        column_names = [description[0] for description in cursor.description]
        return dict(zip(column_names, row))
    except Exception as e:
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()


def get_all_day(db, stock_code, time):
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        time_pattern = f"{str(time[:8])}%"
        query = "SELECT * FROM market_data WHERE stock_code = %s AND time LIKE %s ORDER BY time ASC"
        cursor.execute(query, (str(stock_code), time_pattern,))
        rows = cursor.fetchall()
        column_names = [description[0] for description in cursor.description]
        # Convert each record to a dictionary and return in a list
        return [dict(zip(column_names, row)) for row in rows]
    except Exception as e:
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()