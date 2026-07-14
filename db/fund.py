
def insert_record(db, trans_type: int, money: float, company: str, trans_time: str, tip: str = "", commit_sign = True):
    conn = db.get_connection()
    try:
        sql = """
                INSERT INTO fund (type, money, company, time, tip)
                VALUES (%s, %s, %s, %s, %s)
            """

        # Pack independent parameters into a tuple, matching placeholders in SQL
        params = (trans_type, money, company, trans_time, tip)

        with conn.cursor() as cursor:
            cursor.execute(sql, params)

        # Commit transaction
        conn.commit()

        return cursor.lastrowid

    except Exception as e:
        print(f"Data insertion failed: {e}")
        # Rollback transaction on exception to prevent dirty data
        conn.rollback()
        return None