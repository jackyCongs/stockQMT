# coding=utf-8
import time
def get_stock_list(db, inner_etf_type):
    # Connect to the database
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
    Retrieve stock data in batches using anchor IDs.
    """
    conn = db.get_connection()
    cursor = conn.cursor()
    row_dict_list = []
    try:
        # Core logic: Filter and sort by ID, with LIMIT controlling step size
        query = """
            SELECT * FROM stock 
            WHERE id > %s and source = 'A'
            ORDER BY id ASC 
            LIMIT %s
        """
        cursor.execute(query, (last_id, limit))
        rows = cursor.fetchall()

        # Retrieve column names and map rows to dictionaries
        column_names = [description[0] for description in cursor.description]
        for row in rows:
            row_dict_list.append(dict(zip(column_names, row)))

        return row_dict_list
    except Exception as e:
        print(f"Query error: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


def get_unique_index_codes(db):
    """
    Query all unique target index codes (target_worth_url) that need to be tracked.
    Criteria: status=1 (normal), is_etf=1 (is ETF), and target_worth_url is not empty.

    :param db: Database connection manager object
    :return: list[str], containing all unique index codes. Returns None if an error occurs.
    """
    conn = db.get_connection()
    cursor = conn.cursor()

    try:
        # Core logic: Use DISTINCT to deduplicate, filtering out NULL and empty string values
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

        # rows is a list of tuples, e.g., [('931151',), ('399006',), ('399673',)]
        # Flatten it to a pure string list via list comprehension
        index_codes = [row[0] for row in rows]

        # Logging (optional)
        print(f"Successfully retrieved index codes to track. Total unique targets: {len(index_codes)}")

        return index_codes

    except Exception as e:
        print(f"Error querying index codes: {e}")
        return None
    finally:
        # Ensure cursor and connection are closed properly
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

        # Construct the final SQL query
        # Note: limit 1 is no longer needed since we are updating multiple rows
        sql = f"""
            UPDATE stock 
            SET money = CASE code 
                {' '.join(when_clauses)} 
            END,
            updated_at = CURRENT_TIMESTAMP
            WHERE code IN ({','.join(['%s'] * len(where_codes))})
        """
        # Append WHERE IN arguments to the parameter list
        sql_args.extend(where_codes)
        # Execute the long SQL query
        cursor.execute(sql, sql_args)
        conn.commit()

    except Exception as e:
        print(f"Batch update failed: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


def update(db, stock_name,stock_code,target_worth_name, remark):
    # Connect to the database
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        update_sql = ("UPDATE stock SET "
                      "`name` = %s, "
                      "`target_worth_name` = %s, "
                      "`remark` = %s, "
                      "`updated_at` = CURDATE() "
                      "WHERE `code` = %s")
        # Print current parameters for debugging
        params = (stock_name, target_worth_name, remark, stock_code)
        print(f"Updating data: {params}")
        # Execute update
        cursor.execute(update_sql, params)
        conn.commit()
        print("Update successful")
    except Exception as e:
        print('123456')
        print(e)
        return None
    finally:
        cursor.close()
        conn.close()