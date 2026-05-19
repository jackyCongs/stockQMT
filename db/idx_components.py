import logging

logger = logging.getLogger(__name__)


def save_index_components(db_pool, index_code, components_data):
    """
    带安全熔断机制的量化级增量同步引擎
    :param db_pool: DBPool 数据库连接池对象
    :param index_code: 指数代码 (如 '000300')
    :param components_data: 包含当期最新计算结果的字典列表
    """
    if not components_data:
        logger.warning(f"[{index_code}] 没有要处理的成分股数据。")
        return

    conn = db_pool.get_connection()
    if not conn:
        logger.error("无法获取数据库连接")
        return

    # 提取当前官方名单里所有的最新股票代码
    current_active_stocks = [r['stock_code'] for r in components_data]
    new_count = len(current_active_stocks)

    try:
        with conn.cursor() as cursor:
            # ==========================================
            # 阶段零：【安全熔断检查】防范残缺文件引发的级联误删
            # ==========================================
            cursor.execute("SELECT COUNT(1) FROM idx_components WHERE index_code = %s AND status <> 0", (index_code,))
            old_count = cursor.fetchone()[0]

            # 熔断策略：如果基数大于10只，且今天解析的数量比之前锐减超过 50%，直接拉响警报拦截！
            if old_count > 10 and new_count < old_count * 0.5:
                raise ValueError(f"🔥 安全熔断！[{index_code}] 官方快照疑似残缺！昨日活跃 {old_count} 只，今日仅解析到 {new_count} 只，拒绝执行软删除并整体回滚！")


            # ==========================================
            # 阶段一：批量增量合并 (UPSERT)
            # ==========================================
            upsert_sql = """
                INSERT INTO idx_components (
                    index_code, index_name, stock_code, stock_name, 
                    base_date, weight_percent, base_price, synthetic_shares, status, remark
                ) VALUES (
                    %(index_code)s, %(index_name)s, %(stock_code)s, %(stock_name)s, 
                    %(base_date)s, %(weight_percent)s, %(base_price)s, %(synthetic_shares)s, %(status)s, %(remark)s
                )
                ON DUPLICATE KEY UPDATE
                    index_name       = VALUES(index_name),
                    stock_name       = VALUES(stock_name),
                    base_date        = VALUES(base_date),
                    weight_percent   = VALUES(weight_percent),
                    base_price       = VALUES(base_price),
                    synthetic_shares = VALUES(synthetic_shares),
                    status           = VALUES(status),
                    remark           = VALUES(remark)
            """
            cursor.executemany(upsert_sql, components_data)

            # ==========================================
            # 阶段二：精准物理留痕软删除 (Surgical Soft Delete)
            # ==========================================
            if current_active_stocks:
                format_strings = ','.join(['%s'] * len(current_active_stocks))
                soft_delete_sql = f"""
                    UPDATE idx_components 
                    SET status = 0,
                        remark = %s
                    WHERE index_code = %s 
                      AND stock_code NOT IN ({format_strings})
                      AND status <> 0  -- 避免重复写盘
                """

                # 定义你的硬核死因备注
                audit_reason = f"盘前审计：官方最新权重名单已剔除"

                # 参数拼装
                delete_params = [audit_reason, index_code] + current_active_stocks
                cursor.execute(soft_delete_sql, delete_params)

                if cursor.rowcount > 0:
                    logger.info(f"[{index_code}] 检测到调仓变动！成功对 {cursor.rowcount} 只下架标的执行【软删除留痕】。")

        conn.commit()
        logger.info(f"[{index_code}] 盘前数据增量同步完成。当前官方在册快照: {new_count} 只。")

    except Exception as e:
        conn.rollback()
        logger.error(f"[{index_code}] 合并更新失败，发生事务回滚: {e}")
    finally:
        db_pool.release_connection(conn)