import os
import json
import time
import logging
import warnings
from datetime import datetime, timedelta
import pandas as pd
from xtquant import xtdata
from db.idx_components import save_index_components, get_active_components
from db.index_daily_history import get_index_pre_close

logger = logging.getLogger(__name__)


class IndexReplicationCalculator:
    """
    AlphaCore Pre-market Main Engine (Ultimate Robust Edition)
    Responsible for: File Parsing & Calculation -> Database Incremental Alignment -> JSON Assembly & Output -> Wakeup QMT Subscription
    """

    def __init__(self, db_pool, base_capital=1000000000):
        self.db_pool = db_pool
        self.base_capital = base_capital

        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.project_root = os.path.dirname(current_dir)
        self.download_dir = os.path.join(self.project_root, 'files', 'index_files')
        self.export_path = os.path.join(self.project_root, 'files', 'alphacore_config.json')

    @staticmethod
    def format_component_code(raw_code):
        """[Shared Utility] QMT ticker suffix completion logic for index components"""
        raw_str = str(raw_code).strip().split('.')[0]
        
        # HK Stock Connect tickers are typically 5 digits, no need to pad to 6 digits
        if len(raw_str) == 5 and raw_str.isdigit():
            return f"{raw_str}.HK"
            
        code_str = raw_str.zfill(6)
        if code_str.startswith('60') or code_str.startswith('68') or code_str.startswith('51') or code_str.startswith('56') or code_str.startswith('58'):
            return f"{code_str}.SH"
        elif code_str.startswith('00') or code_str.startswith('30') or code_str.startswith('15'):
            return f"{code_str}.SZ"
        elif code_str.startswith('8') or code_str.startswith('4') or code_str.startswith('92') or code_str.startswith('93'):
            return f"{code_str}.BJ"
        return code_str

    def _ensure_history_data(self, stock_list, period, start_time, end_time, max_retries=15):
        """
        [Precision Sync] Robust synchronous downloader with smart validation to prevent market cap data loss.
        """
        missing_stocks = set(stock_list)

        logger.info(f"⏬ Verifying and downloading {len(missing_stocks)} tickers of {period} market data...")

        for attempt in range(max_retries):
            if not missing_stocks:
                break

            # 1. Attempt local extraction (strict 'front' adjustment to maintain market cap conservation)
            market_data = xtdata.get_market_data_ex(
                field_list=['close'],
                stock_list=list(missing_stocks),
                period=period,
                start_time=start_time,
                end_time=end_time,
                dividend_type='front'
            )

            # 2. Check for missing tickers
            still_missing = set()
            for code in missing_stocks:
                # Condition: Key missing or empty (indicates data downloading/writing is incomplete)
                if code not in market_data or market_data[code].empty:
                    still_missing.add(code)
                else:
                    df_px = market_data[code]
                    if 'close' in df_px.columns:
                        valid_closes = df_px[pd.notna(df_px['close'])]
                        if valid_closes.empty:
                            still_missing.add(code)
                    else:
                        still_missing.add(code)

            if not still_missing:
                logger.info("✅ All historical data has been successfully written to disk (100% complete)!")
                # [Bug Fix] Clear collection before exit to prevent outer scope from reporting ERROR
                missing_stocks = set()
                break

            missing_stocks = still_missing
            logger.warning(f"⏳ Verification attempt {attempt + 1}: {len(missing_stocks)} tickers not ready. Retrying download...")

            # 3. Dispatch asynchronous download requests only for missing tickers
            def dummy_cb(data):
                pass

            for i, qmt_code in enumerate(missing_stocks):
                try:
                    xtdata.download_history_data(qmt_code, period, start_time, end_time, dummy_cb)
                except Exception:
                    pass
                # Rate limit slightly to prevent QMT C++ buffer congestion
                if (i + 1) % 200 == 0:
                    time.sleep(0.1)

            # 4. Force wait 3 seconds to allow C++ to write to local cache before next loop
            time.sleep(3.0)

        if missing_stocks:
            logger.error(
                f"⚠️ Warning! After {max_retries} retries, {len(missing_stocks)} stocks (e.g. {list(missing_stocks)[:5]}) failed to return data (check if delisted)!")

    def _load_dataframe(self, file_path):
        """Unified and robust Excel/CSV data loader"""
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                return pd.read_excel(file_path)
        except ImportError as e:
            logger.error(f"[Environment Error] Missing libraries: {e}, run: pip install xlrd openpyxl")
            return pd.DataFrame()
        except Exception:
            for enc in ['gbk', 'utf-8', 'gb18030', 'latin1']:
                try:
                    df = pd.read_csv(file_path, sep=None, engine='python', encoding=enc)
                    if not df.empty:
                        return df
                except:
                    continue
            return pd.DataFrame()

    def _find_column(self, df, include_keywords, exclude_keywords=None):
        """Smart column name matching with support for excluding specific keywords"""
        if exclude_keywords is None:
            exclude_keywords = []

        for col in df.columns:
            col_str = str(col).lower()
            if any(k in col_str for k in include_keywords):
                if any(ex_k in col_str for ex_k in exclude_keywords):
                    continue
                return col
        return None

    # =====================================================================
    #  Phase 1: File parsing & data preparation (load file -> verify suspensions -> calculate synthetic weights -> database insert)
    # =====================================================================
    def process_all_indices(self):
        """Traverse all downloaded files, perform calculations, and write incremental audit logs to database"""
        if not os.path.exists(self.download_dir):
            logger.error(f"Download directory does not exist: {self.download_dir}")
            return

        files = [f for f in os.listdir(self.download_dir) if f.endswith(('.xls', '.xlsx'))]
        logger.info(f"Processing local weight files. Total: {len(files)} files...")

        for file_name in files:
            parts = file_name.split('_')
            if len(parts) < 2:
                continue

            base_date_str = parts[0]
            index_code = parts[1]
            file_path = os.path.join(self.download_dir, file_name)

            df = self._load_dataframe(file_path)
            if df.empty:
                continue

            col_code = self._find_column(df, ['代码', 'code'], exclude_keywords=['指数', 'index'])
            col_name = self._find_column(df, ['简称', '名称', 'name'], exclude_keywords=['指数', 'index'])
            col_weight = self._find_column(df, ['权重', 'weight'])

            if not col_code or not col_weight:
                logger.warning(f"[{index_code}] Missing key fields (component code/weight). Skipping file.")
                continue

            qmt_stock_list = []
            component_meta = {}

            for index, row in df.iterrows():
                raw_code = str(row[col_code]).strip()
                if raw_code == 'nan' or not raw_code:
                    continue

                raw_name = str(row[col_name]).strip() if col_name else ""
                if raw_name.lower() == 'nan':
                    raw_name = ""

                raw_weight_val = row[col_weight]
                if pd.isna(raw_weight_val):
                    continue

                try:
                    raw_weight = float(raw_weight_val)
                    if pd.isna(raw_weight):
                        continue
                except ValueError:
                    continue

                qmt_code = self.format_component_code(raw_code)
                qmt_stock_list.append(qmt_code)
                component_meta[qmt_code] = {
                    'stock_code': raw_code.split('.')[0].zfill(6),
                    'stock_name': raw_name,
                    'weight_percent': raw_weight
                }

            if not qmt_stock_list:
                continue

            base_dt = datetime.strptime(base_date_str, '%Y%m%d')
            start_dt = base_dt - timedelta(days=90)
            start_search_date_str = start_dt.strftime('%Y%m%d')

            db_date = f"{base_date_str[:4]}-{base_date_str[4:6]}-{base_date_str[6:]}"

            # [Optimization 1]: During data preparation, use safety-batched downloads for large broad-market indices (e.g. CSI 1000/2000)
            self._ensure_history_data(
                stock_list=qmt_stock_list,
                period='1d',
                start_time=start_search_date_str,
                end_time=base_date_str
            )

            # Note: get_market_data_ex queries local storage/RAM without network IO, no lockups, batching unnecessary
            market_data = xtdata.get_market_data_ex(
                field_list=['close'],
                stock_list=qmt_stock_list,
                period='1d',
                start_time=start_search_date_str,
                end_time=base_date_str,
                dividend_type='front'
            )

            db_records = []

            for qmt_code in qmt_stock_list:
                meta = component_meta[qmt_code]

                detail = xtdata.get_instrument_detail(qmt_code)
                if detail is None:
                    status = 0
                    initial_remark = "Pre-market Audit: Missing QMT meta-info / suspected delisted"
                else:
                    status = 1
                    initial_remark = "Official Active: Operating normally"

                base_price = 0.0
                is_suspended_on_base = False

                if qmt_code in market_data and not market_data[qmt_code].empty:
                    df_px = market_data[qmt_code]

                    if base_date_str in df_px.index:
                        px_val = df_px.loc[base_date_str, 'close']
                        if pd.notna(px_val) and float(px_val) > 0:
                            base_price = float(px_val)

                    if base_price <= 0:
                        valid_history_df = df_px[pd.notna(df_px['close'])]
                        if not valid_history_df.empty:
                            base_price = float(valid_history_df['close'].iloc[-1])
                            is_suspended_on_base = True

                synthetic_shares = 0.0
                if base_price > 0:
                    actual_weight = meta['weight_percent'] / 100.0
                    synthetic_shares = (self.base_capital * actual_weight) / base_price

                    if is_suspended_on_base:
                        initial_remark = "Official Active: Suspended on base date (automatically trace back to last active close)"
                else:
                    initial_remark = "Data Anomaly: No valid price returned within 90-day lookback"

                record = {
                    'index_code': index_code,
                    'index_name': '',
                    'stock_code': meta['stock_code'],
                    'stock_name': meta['stock_name'],
                    'base_date': db_date,
                    'weight_percent': meta['weight_percent'],
                    'base_price': base_price,
                    'synthetic_shares': synthetic_shares,
                    'status': status,
                    'remark': initial_remark
                }
                db_records.append(record)

            save_index_components(self.db_pool, index_code, db_records)

    # =====================================================================
    #  Phase 2: Engine Ignition & Launch (pull DB data -> trace close prices -> calculate index divisor -> write JSON -> QMT subscribe)
    # =====================================================================
    def ignite_engine(self, yesterday_str):
        """
        Core ignition workflow: fetch computed data from DB -> output JSON -> trigger QMT subscription.
        :param yesterday_str: Date string for yesterday's trading day, format 'YYYY-MM-DD' (e.g. '2026-05-18')
        """
        logger.info(f"\n=== [AlphaCore] Constructing Golang engine startup payload (T-1 baseline: {yesterday_str}) ===")

        components_map = get_active_components(self.db_pool)
        if not components_map:
            logger.error("No valid component stocks found. Ignition aborted! Please execute process_all_indices() first.")
            return

        pre_close_map = get_index_pre_close(self.db_pool, yesterday_str)
        if not pre_close_map:
            logger.error(f"Failed to retrieve index previous close for {yesterday_str}. Ignition aborted!")
            return

        golang_payload = {}
        all_unique_qmt_stocks = set()

        for stock_list in components_map.values():
            for item in stock_list:
                all_unique_qmt_stocks.add(self.format_component_code(item['stock_code']))

        subscribe_list = list(all_unique_qmt_stocks)

        qmt_yesterday = yesterday_str.replace('-', '')
        yest_dt = datetime.strptime(qmt_yesterday, '%Y%m%d')
        start_yest_dt = yest_dt - timedelta(days=90)
        start_yest_str = start_yest_dt.strftime('%Y%m%d')

        # [Optimization 2]: Safety batching for large-scale multi-thousand stock downloads to avoid deadlocks.
        self._ensure_history_data(
            stock_list=subscribe_list,
            period='1d',
            start_time=start_yest_str,
            end_time=qmt_yesterday
        )
        logger.info("Waiting 5 seconds for underlying data to write to disk...")
        time.sleep(5.0)

        yesterday_market_data = xtdata.get_market_data_ex(
            field_list=['close'],
            stock_list=subscribe_list,
            period='1d',
            start_time=start_yest_str,
            end_time=qmt_yesterday,
            dividend_type='front'
        )

        for index_code, stock_list in components_map.items():
            if index_code not in pre_close_map:
                logger.warning(f"[{index_code}] Missing previous close point. Excluding index from today's active list.")
                continue

            pre_close_point = pre_close_map[index_code]
            formatted_components = {}
            yesterday_synthetic_market_cap = 0.0

            for item in stock_list:
                qmt_code = self.format_component_code(item['stock_code'])
                qi = item['synthetic_shares']
                formatted_components[qmt_code] = qi

                yesterday_price = 0.0
                if qmt_code in yesterday_market_data and not yesterday_market_data[qmt_code].empty:
                    df_px = yesterday_market_data[qmt_code]

                    if qmt_yesterday in df_px.index:
                        px_val = df_px.loc[qmt_yesterday, 'close']
                        if pd.notna(px_val) and float(px_val) > 0:
                            yesterday_price = float(px_val)

                    if yesterday_price <= 0:
                        valid_history_df = df_px[pd.notna(df_px['close'])]
                        if not valid_history_df.empty:
                            yesterday_price = float(valid_history_df['close'].iloc[-1])

                if yesterday_price > 0:
                    yesterday_synthetic_market_cap += yesterday_price * qi
                else:
                    logger.warning(f"[{index_code}] Severe Anomaly: Component {qmt_code} has no trading data for 90 days. Divisor calculation may be distorted!")

            if pre_close_point > 0 and yesterday_synthetic_market_cap > 0:
                divisor = yesterday_synthetic_market_cap / pre_close_point
            else:
                divisor = 1.0

            golang_payload[index_code] = {
                "pre_close": pre_close_point,
                "divisor": divisor,
                "components": formatted_components
            }

        try:
            with open(self.export_path, 'w', encoding='utf-8') as f:
                json.dump(golang_payload, f, ensure_ascii=False, indent=2)
            logger.info(f"✅ Successfully generated AlphaCore config: {self.export_path}")
            logger.info(f"   -> Healthy indices successfully mounted: {len(golang_payload)}")
        except Exception as e:
            logger.error(f"JSON configuration file output failed: {e}")
            return

    # =====================================================================
    #  Unified single-entry runner method
    # =====================================================================
    def run_daily_pipeline(self, yesterday_str):
        """Run both pre-market life cycle phases in one click"""
        self.process_all_indices()
        self.ignite_engine(yesterday_str)