# coding=utf-8
import datetime
import time
import logging
from collections import defaultdict

from xtquant import xtdata
import helper.utils
from db import stock, index_daily_history
from helper import utils, spider, notifier
from helper.data_loader import calc_daily_excess_volatility_batch, get_penalty


def read_lines_to_array(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            # Read all lines and strip trailing newlines/quotes
            lines = [line.strip().strip('"') for line in file]
            return lines
    except FileNotFoundError:
        logger.error(f"Error: File '{file_path}' does not exist.")
        return []
    except Exception as e:
        logger.error(f"Error: Exception occurred while reading file: {e}")
        return []

logger = logging.getLogger(__name__)

class StockService:
    def __init__(self, db):
        self.format_codes = []
        self.stock_codes = []
        self.index_codes = []
        self.success_indices_set = []
        self.db = db
        self.trade_date = ""

    def maintain_sh_etfs(self):
        sh_fund_codes = xtdata.get_stock_list_in_sector('沪深基金')

        print(f"Total funds: {len(sh_fund_codes)}")
        
        if not sh_fund_codes:
            logger.error("Failed to retrieve '沪深基金' sector list from QMT. Please check if QMT terminal is running and connected.")
            return
            
        # Filter out tickers starting with 5 (SSE funds ending with .SH) and query names
        sh_etfs = {}
        for full_code in sh_fund_codes:
            # full_code 形如 "510050.SH"
            if not full_code.endswith('.SH'):
                continue
                
            code = full_code.split('.')[0]
            # Filter strictly for real Exchange Traded Funds (ETFs):
            # SSE ETFs primarily prefix with 51, 52, 53, 56, 58 (501, 502, 505 are LOFs or closed-end funds)
            # Exclude prefix 519 traditional OTC open-end funds (only traded OTC, no PCF list)
            if str(code).startswith(('51', '52', '53', '56', '58')) and not str(code).startswith('519'):
                detail = xtdata.get_instrument_detail(full_code)
                name = detail.get('InstrumentName', code) if detail else code
                
                # Double check: Exclude if 'LOF' is present in the name
                if 'LOF' not in str(name).upper():
                    sh_etfs[code] = name
                
        logger.info(f"QMT retrieved {len(sh_etfs)} pure exchange-traded SSE ETFs")
        
        conn = self.db.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT code, status, remark FROM stock WHERE source = 'A' AND is_etf = 1 AND is_inner_etf = 1 and inner_etf_type='etf'")
                existing = cursor.fetchall()
                existing_map = {}
                if existing:
                    if isinstance(existing[0], dict):
                        existing_map = {row['code']: {'status': row['status'], 'remark': row.get('remark') or ''} for row in existing}
                    else:
                        existing_map = {row[0]: {'status': row[1], 'remark': row[2] if len(row)>2 and row[2] else ''} for row in existing}
                
                new_codes = []
                for code, name in sh_etfs.items():
                    if code not in existing_map:
                        now = datetime.datetime.now()
                        new_codes.append((name, code, 1, 'A', 1, 1, 'etf', 0.5, now, now))
                
                offline_codes = []
                warn_codes = []
                import time
                today_int = int(time.strftime('%Y%m%d'))
                
                for code, info in existing_map.items():
                    status = info['status']
                    if str(code).startswith('5') and code not in sh_etfs and status == 1:
                        # Avoid hasty delisting; invoke QMT instrument details interface for double verification
                        full_code = f"{code}.SH"
                        detail = xtdata.get_instrument_detail(full_code)
                        
                        if not detail:
                            offline_codes.append((f"ETF completely delisted", code))
                        else:
                            expire_date_raw = detail.get('ExpireDate', 99999999)
                            try:
                                expire_date = int(expire_date_raw) if expire_date_raw else 99999999
                            except ValueError:
                                expire_date = 99999999
                                
                            if 0 < expire_date <= today_int:
                                offline_codes.append((f"ETF delisted (Delisting Date: {expire_date})", code))
                            else:
                                warn_codes.append((f"Suspected Anomaly: Ticker absent from SSE/SZSE fund sector list", code))
                        
                online_codes = []
                clear_warn_codes = []
                for code, info in existing_map.items():
                    status = info['status']
                    remark = info['remark']
                    if str(code).startswith('5') and code in sh_etfs:
                        if status != 1 and status != -100:  # Covers status=0, status=-1 etc, but ignore -100
                            online_codes.append(code)
                        elif "Suspected Anomaly" in remark:
                            clear_warn_codes.append(code)
                        
                if new_codes:
                    insert_sql = """
                        INSERT INTO stock (name, code, status, source, is_etf, is_inner_etf, inner_etf_type, withdraw_commission_7rate, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    cursor.executemany(insert_sql, new_codes)
                    logger.info(f"Successfully inserted {len(new_codes)} new SSE ETFs")
                    
                if offline_codes:
                    update_sql = "UPDATE stock SET status = 0, remark = %s, updated_at = NOW() WHERE code = %s"
                    cursor.executemany(update_sql, offline_codes)
                    logger.info(f"Marked {len(offline_codes)} delisted/terminated SSE ETFs with status=0")
                    
                if warn_codes:
                    warn_sql = "UPDATE stock SET remark = %s, updated_at = NOW() WHERE code = %s"
                    cursor.executemany(warn_sql, warn_codes)
                    logger.warning(f"Found {len(warn_codes)} database ETFs missing from sector lists; logged warning remarks.")
                    
                if online_codes:
                    update_online_sql = "UPDATE stock SET status = 1, remark = 'ETF Re-activated', updated_at = NOW() WHERE code = %s"
                    cursor.executemany(update_online_sql, [(code,) for code in online_codes])
                    logger.info(f"Marked {len(online_codes)} re-activated SSE ETFs with status=1")
                    
                if clear_warn_codes:
                    clear_sql = "UPDATE stock SET remark = NULL, updated_at = NOW() WHERE code = %s"
                    cursor.executemany(clear_sql, [(code,) for code in clear_warn_codes])
                    logger.info(f"Cleared warnings for {len(clear_warn_codes)} back-to-normal ETFs")
                    
                if not new_codes and not offline_codes and not online_codes and not warn_codes and not clear_warn_codes:
                    logger.info("Database is already up to date. No updates required.")
                    
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to maintain SSE ETF records: {e}")
        finally:
            conn.close()

    def load_all_stock_codes(self):
        last_id = 0  # Initial anchor ID
        batch_size = 500
        total_processed = 0
        while True:
            # 1. Retrieve current batch
            stocks = stock.get_stock_batch(self.db, last_id, batch_size)
            # Exception: If None returned, DB connection/query errored
            if stocks is None:
                print("Error occurred during query. Aborting process.")
                break
            # 2. Check for data: If result set is empty, query is complete
            if not stocks:
                print("All data processed successfully.")
                break
            # 3. Process current batch records
            for detail in stocks:
                self.stock_codes.append(detail['code'])
            last_id = stocks[-1]['id']
            total_processed += len(stocks)
            print(f"Loaded {total_processed} records. Current ID anchor: {last_id}")

        print(f"Total loaded records: {total_processed}")

    def format_code(self):
        for code in self.stock_codes:
            if utils.enhance_stock_code(code) == code:
                continue
            self.format_codes.append(utils.enhance_stock_code(code))

    def update_stock_price(self):
        self.load_all_stock_codes()
        self.format_code()
        batch_size = 50
        for i in range(0, len(self.format_codes), batch_size):
            batch_codes = self.format_codes[i: i + batch_size]
            seq = xtdata.subscribe_whole_quote(batch_codes, callback=self.stocks_handler)
            logger.info(f"Batch {i // batch_size + 1} subscription successful. Tickers count: {len(batch_codes)}, Sub ID: {seq}")
            time.sleep(2)
        time.sleep(1)
        logger.info("All subscription batch requests dispatched.")

    def stocks_handler(self, msgs):
        update_data = []
        for code in msgs:
            # stock.update_stock_price(self.db, msgs[code]['lastPrice'], helper.utils.purified_code(code))
            update_data.append((helper.utils.purified_code(code), msgs[code]['lastPrice']))
        # 2. 调用批量更新函数
        if update_data:
            stock.batch_update_stock_price(self.db, update_data)
            logger.info(f"Batch update successful: {len(update_data)} stocks")
        logger.info(f"{len(update_data)} records updated successfully")

    def update_index_daily_history(self, fund_spider_cookie):
        """
        Dispatch batch subscriptions to fetch full index snapshots
        """
        # Assumption: index_codes contains codes populated by a method like load_all_index_codes
        # Otherwise, default to standard benchmarks
        self.success_indices_set = set()
        self.index_codes = stock.get_unique_index_codes(self.db)
        benchmark_indices = ['000001', '399001', '000300']
        for code in benchmark_indices:
            if code not in self.index_codes:
                self.index_codes.append(code)
        # 2. Format codes (reuse utils to align with xtdata specifications)
        formatted_indices = [utils.enhance_stock_code(code, "index") for code in self.index_codes]

        # 3. Batch subscription (index count is small, but keep batch logic for safety)
        batch_size = 50
        for i in range(0, len(formatted_indices), batch_size):
            batch_codes = formatted_indices[i: i + batch_size]
            seq = xtdata.subscribe_whole_quote(batch_codes, callback=self.indices_handler)
            logger.info(f"Index batch {i // batch_size + 1} subscribed. Index count: {len(batch_codes)}, Sub ID: {seq}")
            time.sleep(1)  # Lower latency sleep interval since index datasets are small
            
        # --- NEW: Get all active ETFs and subscribe ---
        conn = self.db.get_connection()
        cursor = conn.cursor()
        etf_codes = []
        try:
            cursor.execute("SELECT code FROM stock WHERE is_etf = 1 AND status = 1 AND inner_etf_type = 'etf'")
            etf_rows = cursor.fetchall()
            etf_codes = [row[0] for row in etf_rows]
            formatted_etfs = [helper.utils.enhance_stock_code(code) for code in etf_codes]
            for i in range(0, len(formatted_etfs), batch_size):
                batch_codes = formatted_etfs[i: i + batch_size]
                seq = xtdata.subscribe_whole_quote(batch_codes, callback=self.etf_handler)
                logger.info(f"ETF batch {i // batch_size + 1} subscribed. ETF count: {len(batch_codes)}, Sub ID: {seq}")
                time.sleep(1)
        except Exception as e:
            logger.error(f"Failed to query ETF codes: {e}")
        finally:
            cursor.close()
            conn.close()
        logger.info("Verifying missing indexes. Initiating third-party data updates...")
        time.sleep(20)
        # 1. Set difference: identify missing items that failed to download
        rest_index_codes = list(set(formatted_indices) - self.success_indices_set)

        if rest_index_codes:
            logger.warning(f"Triggering third-party fallback for {len(rest_index_codes)} missing indices: {rest_index_codes}")
            third_party_update_data = []
            for code in rest_index_codes:
                try:
                    clean_code = code.split('.')[0]
                    # 2. Fetch via fallback spider
                    logger.info(f"Fetching snapshot for {clean_code} via third-party interface...")
                    json_data = helper.spider.fetch_single_snapshot_safe(clean_code, fund_spider_cookie)
                    if not json_data:
                        logger.warning(f"Third-party API failed to return data for {code}")
                        continue
                    # 3. Re-use JSON parsing logic
                    parsed_rows = self.parse_third_party_index_json(clean_code, json_data, "EastMoney")
                    if parsed_rows:
                        third_party_update_data.extend(parsed_rows)
                        logger.info(f"Successfully recovered data for {clean_code}.")
                except Exception as e:
                    logger.error(f"Third-party query failed for index {code}: {e}")
                # Safety interval to prevent IP rate-limiting from third-party APIs
                time.sleep(1)
                # Batch insert
            if third_party_update_data:
                index_daily_history.batch_upsert_index_history(self.db, third_party_update_data, 'index')
                logger.info(f"Third-party fallback complete. Recovered {len(third_party_update_data)} data rows.")
        else:
            logger.info("Success! All indices fetched via official feeds. No fallback necessary.")

        logger.info("All subscription batch requests dispatched.")
        # Validate index records
        updated_index_codes = index_daily_history.get_updated_index_codes_by_date(self.db, self.trade_date, 'index')
        if len(self.index_codes) == len(updated_index_codes):
            logger.info(f"✅ Index validation passed: Expected {len(self.index_codes)} rows, actual {len(updated_index_codes)} rows.")
        else:
            # Identify missing tickers
            missing_codes = set(self.index_codes) - set(updated_index_codes)
            alert_msg = (
                f"Trading Date: {self.trade_date}\n"
                f"Expected Index Count: {len(self.index_codes)}\n"
                f"Actual Index Count: {len(updated_index_codes)}\n"
                f"Missing Count: {len(missing_codes)}\n"
                f"Missing List: {list(missing_codes)[:10]}..."
            )
            notifier.send_telegram_alert("🚨 [Alert: Missing Index Data Update]", alert_msg)
            logger.error(alert_msg)

        # Validate ETF records
        updated_etf_codes = index_daily_history.get_updated_index_codes_by_date(self.db, self.trade_date, 'etf')
        if len(etf_codes) == len(updated_etf_codes):
            logger.info(f"✅ ETF validation passed: Expected {len(etf_codes)} rows, actual {len(updated_etf_codes)} rows.")
        else:
            # Identify missing tickers
            missing_etfs = set(etf_codes) - set(updated_etf_codes)
            alert_msg = (
                f"Trading Date: {self.trade_date}\n"
                f"Expected ETF Count: {len(etf_codes)}\n"
                f"Actual ETF Count: {len(updated_etf_codes)}\n"
                f"Missing Count: {len(missing_etfs)}\n"
                f"Missing List: {list(missing_etfs)[:10]}..."
            )
            notifier.send_telegram_alert("🚨 [Alert: Missing ETF Data Update]", alert_msg)
            logger.error(alert_msg)
        # Compute volatility penalties
        self.calculate_and_save_daily_penalty(self.trade_date)

    def calculate_and_save_daily_penalty(self, trade_date):
        """
        Post-market execution: Compile a 3-day history dictionary, feeds it to core engine to calculate penalty rates, and writes to DB.
        Optimization: Evaluates the two days with maximum volatility, discarding the least volatile day.
        Supports newly added assets (computes with available days if data is less than 3 days).
        """
        conn = self.db.get_connection()
        try:
            rows = index_daily_history.get_3_days_history_list(self.db, trade_date)
            if rows is None:
                logger.warning("No historical data found. Skipping penalty calculations.")
                return
            # 3. Group data by index code in memory
            # Layout: {'000001.SH': [0.001, -0.002, 0.005], ...}
            history_map = defaultdict(list)
            for row in rows:
                code, t_date, amplitude = row
                history_map[code].append(float(amplitude))
            # 4. Extract benchmark volatility series (construct index_rates_list)
            # Assumption: standard benchmark indices are used. Skip if data missing.
            benchmarks = ['000001', '399001', '000300']
            index_rates_list = []
            for bm_code in benchmarks:
                if bm_code in history_map and len(history_map[bm_code]) >= 1:
                    index_rates_list.append(history_map[bm_code])
                else:
                    logger.warning(f"Insufficient data for benchmark index {bm_code}. Skipping benchmark.")
            # 5. Process tracked indices and feed into the core engine
            update_penalty_data = []
            for code, fund_rates in history_map.items():
                if len(fund_rates) >= 1:
                    # Step 1: Calculate excess volatility (evaluates up to 3 days, selects the top 2 days)
                    excess_vol = calc_daily_excess_volatility_batch(fund_rates, index_rates_list)
                    # Step 2: Compute final penalty value
                    penalty = get_penalty(excess_vol)
                    # Pack into batch update tuple
                    update_penalty_data.append((round(penalty, 4), code, trade_date))
                    if len(fund_rates) < 3:
                        logger.info(f"[{code}] Has only {len(fund_rates)} days of data. Proceeding with penalty calculation.")
            # 6. Batch update penalty rates in database
            if update_penalty_data:
                index_daily_history.update_penalty_data(self.db, update_penalty_data)
                logger.info(f"Successfully computed and updated penalty values for {len(update_penalty_data)} indices.")
        except Exception as e:
            logger.error(f"Failed to compute historical penalty values: {e}")
            conn.rollback()
        finally:
            conn.close()

    def indices_handler(self, msgs):
        self._process_tick_msgs(msgs, 'index')

    def etf_handler(self, msgs):
        self._process_tick_msgs(msgs, 'etf')

    def _process_tick_msgs(self, msgs, record_type):
        """
        Process incoming index ticks from xtdata and format them for batch insertion
        """
        update_data = []

        for code in msgs:
            tick = msgs[code]

            try:
                # 1. Sanitize code names (reuse utils)
                purified_code = helper.utils.purified_code(code)

                # 2. Extract and format trade dates (convert millisecond timestamps to YYYY-MM-DD)
                # Tolerance handling: verify 'time' field exists (especially for weekend testing)
                timestamp_ms = tick.get('time', 0)
                if timestamp_ms == 0:
                    continue  # Skip invalid data
                trade_date = datetime.datetime.fromtimestamp(timestamp_ms / 1000.0).strftime('%Y-%m-%d')
                if self.trade_date == "":
                    self.trade_date = trade_date

                # 3. Extract L1 orderbook details
                close_price = tick.get('lastPrice', 0.0)
                pre_close = tick.get('lastClose', 0.0)
                open_price = tick.get('open', 0.0)
                high_price = tick.get('high', 0.0)
                low_price = tick.get('low', 0.0)
                volume = tick.get('volume', 0)
                amount = tick.get('amount', 0.0)

                # 4. Pre-calculate risk parameters: actual daily percentage change
                if pre_close and pre_close > 0:
                    vol_rate = round((close_price - pre_close) / pre_close, 4)
                else:
                    vol_rate = 0.0

                # 5. Pack as tuple, matching parameter order of batch_upsert_index_history
                row_tuple = (purified_code, trade_date, close_price, pre_close, open_price, high_price, low_price, vol_rate, volume, amount, 'QMT')
                update_data.append(row_tuple)
                if not hasattr(self, 'success_indices_set') and record_type == 'index':
                    self.success_indices_set = set()
                if record_type == 'index':
                    # Note: Record raw unsubscribed code to identify set differences later
                    self.success_indices_set.add(code)

            except Exception as e:
                logger.error(f"Exception during processing tick feed for {code}: {e}")
                continue
        # 6. Invoke batch upsert interface
        if update_data:
            index_daily_history.batch_upsert_index_history(self.db, update_data, record_type)
            logger.info(f"Batch upsert successful: {len(update_data)} {record_type} records")

    def parse_third_party_index_json(self, index_code, json_data, data_source):
        """
        Parse index JSON data from third-party APIs (e.g. EastMoney) and return formatted tuples for batch insertion.

        :param json_data: dict, Complete response JSON from third-party API
        :return: list[tuple], Single-row list containing parsed records, ready for batch_upsert_index_history. Returns empty list if parsing fails.
        """
        try:
            # Retrieve core data payload
            data = json_data.get("data")
            if not data:
                logger.warning("No 'data' field found in third-party payload")
                return []
            timestamp_sec = data.get("f86", 0)
            if timestamp_sec == 0:
                return []
            trade_date = datetime.datetime.fromtimestamp(timestamp_sec).strftime('%Y-%m-%d')

            # 3. Extract OHLCV data (Note: price values are scaled by 100 in raw payload, divide by 100)
            close_price = data.get("f43", 0) / 100.0  # 最新价
            high_price = data.get("f44", 0) / 100.0  # 最高价
            low_price = data.get("f45", 0) / 100.0  # 最低价
            open_price = data.get("f46", 0) / 100.0  # 开盘价
            pre_close = data.get("f60", 0) / 100.0  # 昨收价

            volume = data.get("f47", 0)  # 成交量
            amount = data.get("f48", 0.0)  # 成交额

            # 4. Calculate daily price change rate
            if pre_close and pre_close > 0:
                vol_rate = round((close_price - pre_close) / pre_close, 4)
            else:
                vol_rate = 0.0

            # 5. Match parameter order required by batch_upsert_index_history
            row_tuple = (index_code, trade_date, close_price, pre_close, open_price, high_price, low_price, vol_rate, volume, amount, data_source)

            # Pack as list to reuse batch upsert interface
            return [row_tuple]

        except Exception as e:
            logger.error(f"Failed to parse third-party index data: {e}, raw: {json_data}")
            return []
