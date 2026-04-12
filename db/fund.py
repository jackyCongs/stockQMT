
def insert_record(db, trans_type: int, money: float, company: str, trans_time: str, tip: str = "", commit_sign = True):
    conn = db.get_connection()
    try:
        sql = """
                INSERT INTO fund (type, money, company, time, tip)
                VALUES (%s, %s, %s, %s, %s)
            """

        # 将独立的入参直接打包为元组，严格对应 SQL 中的占位符
        params = (trans_type, money, company, trans_time, tip)

        with conn.cursor() as cursor:
            cursor.execute(sql, params)

        # 提交事务
        conn.commit()

        return cursor.lastrowid

    except Exception as e:
        print(f"数据插入失败: {e}")
        # 发生异常时回滚事务，防止产生脏数据
        conn.rollback()
        return None