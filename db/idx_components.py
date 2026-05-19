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


def get_active_components(db_pool):
    """
    获取所有健康的指数成分股。
    【核心风控】自动过滤掉包含港股、数据缺失的“污染指数”，并高亮打印提醒。
    :return: dict, 结构如 {'000300': [{'stock_code': '600519', 'synthetic_shares': 1234.5}, ...]}
    """
    conn = db_pool.get_connection()
    if not conn:
        logger.error("无法获取数据库连接")
        return {}

    components_map = {}
    try:
        with conn.cursor() as cursor:
            # ==========================================
            # 1. 侦测阶段：揪出所有“被污染”的异常指数
            # 污染特征：虚拟股数为0 / 价格为0 / (状态为0 且 并非被官方主动剔除)
            # ==========================================
            # 注意：PyMySQL 中使用 %% 来转义 % 符号
            tainted_sql = """
                SELECT DISTINCT index_code 
                FROM idx_components 
                WHERE synthetic_shares <= 0 
                   OR base_price <= 0 
                   OR (status = 0 AND remark NOT LIKE '%%剔除%%')
            """
            cursor.execute(tainted_sql)
            tainted_rows = cursor.fetchall()

            # 兼容游标返回的是 dict 还是 tuple
            tainted_indices = []
            if tainted_rows:
                if isinstance(tainted_rows[0], dict):
                    tainted_indices = [row['index_code'] for row in tainted_rows]
                else:
                    tainted_indices = [row[0] for row in tainted_rows]

            # 触发强提醒，让你对摘除的指数心中有数
            if tainted_indices:
                logger.warning(f"⚠️ 【风控拦截】发现 {len(tainted_indices)} 个指数包含港股或严重数据缺失！")
                logger.warning(f"⚠️ 为防止盘中算力污染与巨大跟踪误差，已将以下指数整体摘除不再挂载: {tainted_indices}")
            else:
                logger.info("✅ 盘前完整性审计通过，未发现受污染的异常指数。")

            # ==========================================
            # 2. 提取阶段：只拿取“健康指数”里的正常成分股
            # ==========================================
            if tainted_indices:
                # 动态拼接 NOT IN 条件，屏蔽掉所有受污染的 index_code
                format_strings = ','.join(['%s'] * len(tainted_indices))
                healthy_sql = f"""
                    SELECT index_code, stock_code, synthetic_shares 
                    FROM idx_components 
                    WHERE status = 1 AND index_code NOT IN ({format_strings})
                """
                cursor.execute(healthy_sql, tuple(tainted_indices))
            else:
                healthy_sql = """
                    SELECT index_code, stock_code, synthetic_shares 
                    FROM idx_components 
                    WHERE status = 1
                """
                cursor.execute(healthy_sql)

            rows = cursor.fetchall()

            for row in rows:
                # 兼容游标类型
                idx_code = row['index_code'] if isinstance(row, dict) else row[0]
                stk_code = row['stock_code'] if isinstance(row, dict) else row[1]
                shares = row['synthetic_shares'] if isinstance(row, dict) else row[2]

                if idx_code not in components_map:
                    components_map[idx_code] = []
                components_map[idx_code].append({
                    'stock_code': stk_code,
                    'synthetic_shares': float(shares)
                })

        return components_map
    except Exception as e:
        logger.error(f"提取有效成分股失败: {e}")
        return {}
    finally:
        # 顺手帮你优化了一下，之前这里是 conn.close()，使用连接池应该用 release_connection
        if hasattr(db_pool, 'release_connection'):
            db_pool.release_connection(conn)
        else:
            conn.close()