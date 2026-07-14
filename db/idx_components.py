import logging

logger = logging.getLogger(__name__)


def save_index_components(db_pool, index_code, components_data):
    """
    Quantitative incremental synchronization engine with safety circuit breaker.
    :param db_pool: DBPool database connection pool object
    :param index_code: Index code (e.g., '000300')
    :param components_data: List of dictionaries containing the latest calculation results
    """
    if not components_data:
        logger.warning(f"[{index_code}] No component stock data to process.")
        return

    conn = db_pool.get_connection()
    if not conn:
        logger.error("Failed to acquire database connection")
        return

    # Extract all current active stock codes from the official snapshot
    current_active_stocks = [r['stock_code'] for r in components_data]
    new_count = len(current_active_stocks)

    try:
        with conn.cursor() as cursor:
            # ==========================================
            # Phase 0: [Safety Circuit Breaker Check] Prevent cascade deletions caused by incomplete source files
            # ==========================================
            cursor.execute("SELECT COUNT(1) FROM idx_components WHERE index_code = %s AND status <> 0", (index_code,))
            old_count = cursor.fetchone()[0]

            # Circuit breaker policy: if the baseline exceeds 10 stocks and today's parsed count drops by more than 50%, trigger an immediate abort!
            if old_count > 10 and new_count < old_count * 0.5:
                raise ValueError(f"🔥 Safety Circuit Breaker Triggered! [{index_code}] Official snapshot appears incomplete. Yesterday active: {old_count}, today parsed: {new_count}. Aborting soft-delete and rolling back transaction.")


            # ==========================================
            # Phase 1: Batch Upsert (UPSERT)
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
            # Phase 2: Surgical Soft Delete with Audit Trail
            # ==========================================
            if current_active_stocks:
                format_strings = ','.join(['%s'] * len(current_active_stocks))
                soft_delete_sql = f"""
                    UPDATE idx_components 
                    SET status = 0,
                        remark = %s
                    WHERE index_code = %s 
                      AND stock_code NOT IN ({format_strings})
                      AND status <> 0  -- Avoid redundant disk writes
                """

                # Define audit reason for exclusion
                audit_reason = f"Pre-market Audit: Excluded from the latest official index weights"

                # Assemble parameters
                delete_params = [audit_reason, index_code] + current_active_stocks
                cursor.execute(soft_delete_sql, delete_params)

                if cursor.rowcount > 0:
                    logger.info(f"[{index_code}] Rebalancing detected. Successfully executed soft delete for {cursor.rowcount} excluded tickers.")

        conn.commit()
        logger.info(f"[{index_code}] Pre-market data incremental synchronization complete. Current active official snapshot count: {new_count} tickers.")

    except Exception as e:
        conn.rollback()
        logger.error(f"[{index_code}] Upsert failed, rolling back transaction: {e}")
    finally:
        conn.close()


def get_active_components(db_pool):
    """
    Retrieve all healthy index component stocks.
    [Core Risk Control] Automatically filters out "polluted indices" containing HK stocks or missing data, and logs warnings.
    :return: dict, structured as {'000300': [{'stock_code': '600519', 'synthetic_shares': 1234.5}, ...]}
    """
    conn = db_pool.get_connection()
    if not conn:
        logger.error("Failed to acquire database connection")
        return {}

    components_map = {}
    try:
        with conn.cursor() as cursor:
            # ==========================================
            # 1. Detection Phase: Identify all "polluted" abnormal indices
            # Polluted criteria: synthetic shares = 0 / price = 0 / (status = 0 and not explicitly excluded by official rebalancing)
            # ==========================================
            # Note: Use %% to escape % in PyMySQL queries
            tainted_sql = """
                            SELECT DISTINCT index_code 
                            FROM idx_components 
                            WHERE (status = 1 AND base_price <= 0)
                               OR (status = 1 AND synthetic_shares <= 0 AND weight_percent > 0)
                               OR (status = 0 AND remark NOT LIKE '%%剔除%%' AND remark NOT LIKE '%%Excluded%%')
                        """
            cursor.execute(tainted_sql)
            tainted_rows = cursor.fetchall()

            # Handle cursor compatibility (dict vs tuple)
            tainted_indices = []
            if tainted_rows:
                if isinstance(tainted_rows[0], dict):
                    tainted_indices = [row['index_code'] for row in tainted_rows]
                else:
                    tainted_indices = [row[0] for row in tainted_rows]

            # Trigger warnings for excluded indices
            if tainted_indices:
                logger.warning(f"⚠️ [Risk Control Interception] Found {len(tainted_indices)} indices containing HK stocks or exhibiting severe data loss!")
                logger.warning(f"⚠️ To prevent intraday data pollution and large tracking errors, the following indices have been excluded: {tainted_indices}")
            else:
                logger.info("✅ Pre-market integrity audit passed. No polluted or abnormal indices detected.")

            # ==========================================
            # 2. Extraction Phase: Fetch normal component stocks from healthy indices only
            # ==========================================
            if tainted_indices:
                # Dynamically construct NOT IN condition to exclude tainted indices
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
                # Handle cursor type compatibility
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
        logger.error(f"Failed to extract active component stocks: {e}")
        return {}
    finally:
        conn.close()