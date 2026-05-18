import logging

logger = logging.getLogger(__name__)


def save_index_components(db_pool, index_code, components_data):
    """
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

    # 提取当前官方名单里所有的最新股票代码，用于第二阶段的“精准软删除判定”
    current_active_stocks = [r['stock_code'] for r in components_data]

    try:
        with conn.cursor() as cursor:
            # ==========================================
            # 阶段一：批量增量合并 (UPSERT)
            # ==========================================
            # 包含新加入的 remark 字段。如果某股票之前被软删除了，这次又被纳入，
            # 这里的 VALUES(status) 会自动将其复活为 1，并将备注更新为最新状态。
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
            # 逻辑：数据库中原有的标的，如果不在今天最新的官方 CSV 名单中，且状态还是“正常(<>0)”，
            # 则将其 status 置为 0，并在 remark 字段中记录死因和审计时间。
            if current_active_stocks:
                format_strings = ','.join(['%s'] * len(current_active_stocks))
                soft_delete_sql = f"""
                    UPDATE idx_components 
                    SET status = 0,
                        remark = %s
                    WHERE index_code = %s 
                      AND stock_code NOT IN ({format_strings})
                      AND status <> 0  -- 核心性能优化：已经是软删除状态的不再重复改写，避免无意义 I/O
                """

                # 定义你的硬核死因备注
                audit_reason = f"盘前审计：官方最新权重名单已剔除"

                # 参数拼装: [备注原因, index_code, '600519', '000001', ...]
                delete_params = [audit_reason, index_code] + current_active_stocks
                cursor.execute(soft_delete_sql, delete_params)

                if cursor.rowcount > 0:
                    logger.info(f"[{index_code}] 检测到调仓变动！成功对 {cursor.rowcount} 只下架标的执行【软删除留痕】。")

        conn.commit()
        logger.info(f"[{index_code}] 盘前数据增量同步完成。当前官方在册快照: {len(components_data)} 只。")

    except Exception as e:
        conn.rollback()
        logger.error(f"[{index_code}] 软删除合并更新失败，发生事务回滚: {e}")
    finally:
        db_pool.release_connection(conn)