# service/etf_alphacore_config_service.py
import json
import os
import sys
import datetime
import threading
import pandas as pd
from xtquant import xtdata
import time
from db import index_daily_history
from db import stock as stock_db
from helper import utils
from service.pcf.pcf_provider import PcfProvider
from service.pcf.sse_pcf_provider import SsePcfProvider
from service.pcf.szse_pcf_provider import SzsePcfProvider

# Force stdout/stderr to use UTF-8 to prevent encoding errors when printing to Windows terminal
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass


class ETFAlphaCoreConfigService:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        # Singleton pattern
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ETFAlphaCoreConfigService, cls).__new__(cls)
            return cls._instance

    def __init__(self, db, yesterday_date):
        self.etf_target_index_map = None
        if hasattr(self, 'initialized'):
            return
        self.initialized = True
        self._pcf_info_cache = {}
        self._pcf_comp_cache = {}
        self.pcf_fetch_failures = []
        self.db = db
        # PCF data providers (sharing pcf_fetch_failures list)
        self._sse_provider = SsePcfProvider(pcf_fetch_failures=self.pcf_fetch_failures)
        self._szse_provider = SzsePcfProvider(pcf_fetch_failures=self.pcf_fetch_failures)
        self.yesterday_date = yesterday_date
        
        # Default config file path
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.default_config_path = os.path.join(project_root, "files", "alphacore_config.json")

    def _ensure_history_data(self, stock_list, period, start_time, end_time, max_retries=5):
        """Synchronous downloader to ensure all stock market data is cached locally"""
        missing_stocks = set(stock_list)
        print(f"  ⏬ Verifying and downloading {len(missing_stocks)} tickers of {period} market data...")
        
        for attempt in range(max_retries):
            if not missing_stocks:
                break
                
            market_data = xtdata.get_market_data_ex(
                field_list=['close'],
                stock_list=list(missing_stocks),
                period=period,
                start_time=start_time,
                end_time=end_time,
                dividend_type='none'
            )
            
            still_missing = set()
            for code in missing_stocks:
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
                print("  ✅ All historical data has been successfully written to disk (100% complete)!")
                missing_stocks = set()
                break
                
            missing_stocks = still_missing
            print(f"  ⏳ Verification attempt {attempt + 1}: {len(missing_stocks)} tickers not ready. Retrying download...")
            
            def dummy_cb(data):
                pass
                
            try:
                xtdata.download_history_data2(list(missing_stocks), period, start_time, end_time, dummy_cb)
            except Exception as e:
                print(f"  ❌ Failed to invoke batch history download: {e}")
                
            time.sleep(3.0)
            
        if missing_stocks:
            print(f"  ⚠️ Warning: {len(missing_stocks)} stocks (e.g. {list(missing_stocks)[:5]}) failed to return data (may be suspended or delisted)!")

    def _get_pcf_provider(self, fund_code: str) -> PcfProvider:
        """Retrieve the PCF data provider for the corresponding exchange based on fund code"""
        if fund_code.startswith("1"):
            return self._szse_provider
        return self._sse_provider

    def get_pcf_basic_info(self, fund_code: str) -> dict | None:
        """Retrieve basic ETF Portfolio Composition File (PCF) metadata (cash component, unit NAV, etc. for SSE & SZSE)"""
        return self._get_pcf_provider(fund_code).get_basic_info(fund_code)

    def get_pcf_components(self, fund_code: str) -> pd.DataFrame | None:
        """Retrieve ETF PCF components dataframe (supporting both SSE & SZSE)"""
        return self._get_pcf_provider(fund_code).get_components(fund_code)

    def generate_config(self, fund_code: str, current_idx: int = 0, total_count: int = 0):
        """
        Generate alphacore_config.json configuration from ETF PCF list.
        """
        print("=" * 70)
        progress_str = f" [{current_idx}/{total_count}]" if total_count > 0 else ""
        print(f"  Generating alphacore config for {fund_code}{progress_str}")
        print("=" * 70)

        # ---- 1. 获取 PCF 成分股 ----
        print("\n[1/4] Retrieving PCF snapshot...")
        if fund_code in self._pcf_info_cache:
            info = self._pcf_info_cache[fund_code]
        else:
            info = self.get_pcf_basic_info(fund_code)
            
        if fund_code in self._pcf_comp_cache:
            comp_df = self._pcf_comp_cache[fund_code]
        else:
            comp_df = self.get_pcf_components(fund_code)

        if comp_df is None or comp_df.empty:
            print("  ❌ Failed to retrieve PCF components. Aborting.")
            return

        # Filter physical shares: exclude SUBSTITUTION_FLAG="2" (must substitute with cash)
        physical_df = comp_df[comp_df["SUBSTITUTION_FLAG"] != "2"].copy()
        cash_only_df = comp_df[comp_df["SUBSTITUTION_FLAG"] == "2"]
        print(f"  Total PCF components: {len(comp_df)}")
        print(f"  Physical stocks: {len(physical_df)} (cash substitution = allowed)")
        print(f"  Cash substitution only: {len(cash_only_df)} (cash substitution = mandatory, excluded)")

        components = {}
        hk_stocks = []
        non_a_shares = []
        for _, row in physical_df.iterrows():
            raw_code = str(row["INSTRUMENT_ID"]).strip().split('.')[0]
            market_id = str(row.get("UNDERLYION_SECURITY_ID", "")).strip()

            # 103 represents HK market
            if market_id == "103" or (len(raw_code) == 5 and raw_code.isdigit()):
                # Standardize HK stock code to 5 digits
                code = raw_code.zfill(5)
                suffix = ".HK"
                hk_stocks.append(f"{code}{suffix}")
                stock_code = f"{code}{suffix}"
            else:
                # Strict check for normal A-shares. Avoid blindly calling zfill(6) which incorrectly turns '981' to '000981' (erroneously marked as SZSE A-share).
                # Official exchange codes for A-shares will always return complete 6-digit strings (e.g. '000001').
                if not utils.is_normal_a_share(raw_code):
                    stock_code = raw_code
                    non_a_shares.append(stock_code)
                else:
                    stock_code = utils.enhance_stock_code(raw_code)
                    
            quantity = int(row["QUANTITY"])
            components[stock_code] = quantity

        print(f"  Components mapping generated: {len(components)} stocks")
        
        if not components:
            print("  🚫 No valid physical A-share components found for this ETF (likely cash-only substitution or overseas fund). Skipping generation.")
            return {"skipped": True, "reason": "No valid physical A-share components"}

        if hk_stocks or non_a_shares:
            skip_reasons = []
            if hk_stocks:
                skip_reasons.append(f"{len(hk_stocks)} HK stocks")
            if non_a_shares:
                skip_reasons.append(f"{len(non_a_shares)} non-A shares (bonds/gold/commodities, etc.)")
                
            print(f"  🚫 Found {', '.join(skip_reasons)}. Skipping alphacore configuration for this ETF.")
            return {"skipped": True, "hk_stocks": hk_stocks, "non_a_shares": non_a_shares}

        # ---- 2. 获取昨收价，计算 basket_pre_close ----
        print("\n[2/4] Retrieving previous close prices and calculating basket_pre_close...")
        basket_pre_close = 0.0
        missing_count = 0
        stock_list = list(components.keys())
        market_data = {}
        
        qmt_yesterday = self.yesterday_date.replace('-', '')
        
        # To optimize speed, we fetch market data in two phases:
        # Phase 1: Batch download 1d history data for yesterday
        try:
            market_data = xtdata.get_market_data_ex(
                field_list=['close'],
                stock_list=stock_list,
                period='1d',
                start_time=qmt_yesterday,
                end_time=qmt_yesterday,
                dividend_type='none'
            )
        except Exception as e:
            print(f"  ❌ Phase 1 QMT get_market_data_ex call failed: {e}")

        missing_stocks_info = []
        stock_last_close = {}

        # Attempt to extract Phase 1 close prices
        for stock_code in stock_list:
            last_close = 0.0
            if stock_code in market_data and not market_data[stock_code].empty:
                try:
                    df_px = market_data[stock_code]
                    if 'close' in df_px.columns:
                        valid_closes = df_px[pd.notna(df_px['close'])]
                        if not valid_closes.empty:
                            last_close = float(valid_closes['close'].iloc[-1])
                except Exception:
                    pass
            stock_last_close[stock_code] = last_close
            if last_close <= 0:
                missing_stocks_info.append(stock_code)

        # Phase 2: If any stocks still lack a close price (likely suspended), run a 60-day lookback query for them
        if missing_stocks_info:
            print(f"  ⏳ Launching 60-day historical lookback for {len(missing_stocks_info)} suspended/missing-data tickers...")
            yesterday_dt = datetime.datetime.strptime(qmt_yesterday, '%Y%m%d')
            start_dt = yesterday_dt - datetime.timedelta(days=60)
            qmt_start_date = start_dt.strftime('%Y%m%d')

            try:
                self._ensure_history_data(
                    stock_list=missing_stocks_info,
                    period='1d',
                    start_time=qmt_start_date,
                    end_time=qmt_yesterday
                )
                fallback_market_data = xtdata.get_market_data_ex(
                    field_list=['close'],
                    stock_list=missing_stocks_info,
                    period='1d',
                    start_time=qmt_start_date,
                    end_time=qmt_yesterday,
                    dividend_type='none'
                )
                
                # Re-extract close prices for these lookback tickers
                for stock_code in missing_stocks_info:
                    if stock_code in fallback_market_data and not fallback_market_data[stock_code].empty:
                        try:
                            df_px = fallback_market_data[stock_code]
                            if 'close' in df_px.columns:
                                valid_closes = df_px[pd.notna(df_px['close'])]
                                if not valid_closes.empty:
                                    stock_last_close[stock_code] = float(valid_closes['close'].iloc[-1])
                        except Exception:
                            pass
            except Exception as e:
                print(f"  ❌ Phase 2 QMT get_market_data_ex call failed: {e}")

        # Aggregate final prices and compute cumulative basket value
        missing_count = 0
        final_missing_stocks = []
        for stock_code, qty in components.items():
            last_close = stock_last_close.get(stock_code, 0.0)
            if last_close > 0:
                basket_pre_close += last_close * qty
            else:
                missing_count += 1
                final_missing_stocks.append(stock_code)

        if missing_count > 0:
            print(f"  ⚠ {missing_count} tickers did not return a valid previous close (e.g. {', '.join(final_missing_stocks[:5])})")
        basket_pre_close = round(basket_pre_close, 2)
        print(f"  basket_pre_close = {basket_pre_close}")

        # ---- 3. 获取指数昨收 ----
        print("\n[3/4] Fetching index previous close...")
        index_code = 0
        index_pre_close = 0.0
        if self.etf_target_index_map and fund_code in self.etf_target_index_map:
            index_data = self.etf_target_index_map[fund_code]
            index_code = index_data["index_code"]
            index_pre_close = index_data["index_pre_price"]
        
        if index_pre_close > 0:
            print(f"  Index {index_code} previous close: {index_pre_close}")
        else:
            print(" ❌️ ⚠ Failed to retrieve index previous close. Defaulting to 0.0 placeholder.")

        # ---- 4. 写入配置文件 ----
        print("\n[4/4] Saving configuration...")
        config_path = self.default_config_path

        estimated_cash = PcfProvider.clean_float(info.get("ESTIMATED_CASH_COMPONENT", 0.0)) if info else 0.0
        net_asset_value = PcfProvider.clean_float(info.get("NAV", 0.0)) if info else 0.0

        # Extract raw asset value from PCF metadata
        origin_basket_amount_raw = info.get("NAVPERCU") or info.get("最小申购、赎回单位资产净值") or 0.0
        origin_basket_amount = PcfProvider.clean_float(origin_basket_amount_raw)
        
        # Current date string
        today_str = datetime.datetime.now().strftime("%Y%m%d")

        # Load, append, and overwrite configuration file
        config_data = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
            except Exception:
                pass

        config_data[fund_code] = {
            "index_code": index_code,
            "index_pre_close": index_pre_close,
            "basket_pre_close": basket_pre_close,
            "estimated_cash": estimated_cash,
            "net_asset_value": net_asset_value,
            "origin_basket_amount": origin_basket_amount,
            "hidden_substitute_amount": round(origin_basket_amount-estimated_cash-basket_pre_close, 5),
            "update_date": today_str,
            "components": components,
        }

        # Ensure directory structure exists
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)

        print(f"  Configuration saved to: {config_path}")
        print(f"\n  --- Config Summary ---")
        print(f"  index_code:           {index_code}")
        print(f"  index_pre_close:      {index_pre_close}")
        print(f"  basket_pre_close:     {basket_pre_close}")
        print(f"  estimated_cash:       {estimated_cash}")
        print(f"  net_asset_value:      {net_asset_value}")
        print(f"  origin_basket_amount: {origin_basket_amount}")
        print(f"  hidden_substitute_amount:{origin_basket_amount-estimated_cash-basket_pre_close}")
        print(f"  update_date:          {today_str}")
        print(f"  components:           {len(components)} physical stocks")

        print("\n" + "=" * 70)
        print("  Completed")
        print("=" * 70)
        
        return {"skipped": False}

    def verify_config(self):
        print("\n" + "=" * 70)
        print("  Initiating config validation...")
        print("=" * 70)
        
        if not os.path.exists(self.default_config_path):
            print(f"  ❌ Validation failed: Configuration file {self.default_config_path} does not exist.")
            return

        try:
            with open(self.default_config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as e:
            print(f"  ❌ Validation failed: Error reading configuration file: {e}")
            return

        today_str = datetime.datetime.now().strftime("%Y%m%d")
        outdated_funds = []

        for fund_code, data in config_data.items():
            # Validate update_date
            update_date = data.get("update_date", "")
            if update_date != today_str:
                outdated_funds.append((fund_code, update_date))

        # Output results with formatting
        has_error = len(outdated_funds) > 0
        RED = "\033[1;31m"
        GREEN = "\033[1;32m"
        YELLOW = "\033[1;33m"
        CYAN = "\033[1;36m"
        BOLD = "\033[1m"
        RESET = "\033[0m"

        if has_error:
            print("\n" + RED + "🚨" * 40 + RESET)
            print(RED + "🚨" + " " * 24 + BOLD + "【 Configuration Validation Failed 】" + " " * 24 + "🚨" + RESET)
            print(RED + "🚨" * 40 + RESET)
            
            if outdated_funds:
                print(YELLOW + BOLD + "\n  👉 [ Outdated / Missing Date Anomalies ]" + RESET)
                for fund_code, u_date in outdated_funds:
                    if not u_date:
                        print(RED + BOLD + f"    ✗ Fund [{fund_code}]: Missing 'update_date' field, please review generation logic!" + RESET)
                    else:
                        print(RED + BOLD + f"    ✗ Fund [{fund_code}]: Date is {u_date} (expected: {today_str}) [Outdated / Not updated!]" + RESET)
            print("\n" + RED + "🚨" * 40 + RESET)
        else:
            print("\n" + GREEN + "======================================================================" + RESET)
            print(GREEN + BOLD + "  ✓ Configuration validation passed! All fund dates are up to date!" + RESET)
            print(GREEN + "======================================================================" + RESET)

    def run(self):
        self.pcf_fetch_failures = []
        # Sync error list reference for both providers
        self._sse_provider.pcf_fetch_failures = self.pcf_fetch_failures
        self._szse_provider.pcf_fetch_failures = self.pcf_fetch_failures
        fund_codes = []

        stocks = stock_db.get_stock_list(self.db, 'etf')
        for stock in stocks:
            code = stock['code']
            # Currently only SSE funds (prefix 5) are allowed into the Alphacore pipeline
            if code.startswith('5'):
                fund_codes.append(code)

        self.etf_target_index_map = index_daily_history.get_etf_target_index_pre_close(self.db, fund_codes, self.yesterday_date)
        if not fund_codes:
            print("  ⚠ No ETF codes provided. Skipping configuration generation.")
            return
            
        print("\n" + "=" * 70)
        print(f"  [Preprocessing] Aggregating all ETF components and pre-downloading market data...")
        print("=" * 70)
        
        all_required_stocks = set()
        self._pcf_info_cache = {}
        self._pcf_comp_cache = {}
        
        total_funds = len(fund_codes)
        for i, fund_code in enumerate(fund_codes):
            print(f"  ⏳ [{i+1}/{total_funds}] Retrieving and parsing PCF list for ETF {fund_code}...")
            info = self.get_pcf_basic_info(fund_code)
            comp_df = self.get_pcf_components(fund_code)
            self._pcf_info_cache[fund_code] = info
            self._pcf_comp_cache[fund_code] = comp_df
            
            if comp_df is not None and not comp_df.empty:
                physical_df = comp_df[comp_df["SUBSTITUTION_FLAG"] != "2"]
                for _, row in physical_df.iterrows():
                    raw_code = str(row["INSTRUMENT_ID"]).strip().split('.')[0]
                    market_id = str(row.get("UNDERLYION_SECURITY_ID", "")).strip()
                    if market_id == "103" or (len(raw_code) == 5 and raw_code.isdigit()):
                        continue
                    else:
                        if utils.is_normal_a_share(raw_code):
                            all_required_stocks.add(utils.enhance_stock_code(raw_code))

        if all_required_stocks:
            qmt_yesterday = self.yesterday_date.replace('-', '')
            print(f"  ⏬ Extracted {len(all_required_stocks)} unique tickers. Starting batch pre-download for {qmt_yesterday} data...")
            def dummy_cb(data):
                pass
            try:
                xtdata.download_history_data2(list(all_required_stocks), '1d', qmt_yesterday, qmt_yesterday, dummy_cb)
                print(f"  ✅ Pre-download complete! Waiting 5 seconds for disk write...")
                time.sleep(5.0)
            except Exception as e:
                print(f"  ❌ Batch pre-download failed: {e}")

        print("\n" + "=" * 70)
        print(f"  Generating ETF configuration files (Total: {len(fund_codes)} funds)...")
        print("=" * 70)
        
        skipped_hk_etfs = {}
        skipped_non_a_etfs = {}
        total_funds = len(fund_codes)
        for i, fund_code in enumerate(fund_codes):
            try:
                res = self.generate_config(fund_code, current_idx=i+1, total_count=total_funds)
                if res and res.get("skipped"):
                    if res.get("hk_stocks"):
                        skipped_hk_etfs[fund_code] = res.get("hk_stocks", [])
                    if res.get("non_a_shares"):
                        skipped_non_a_etfs[fund_code] = res.get("non_a_shares", [])
            except Exception as e:
                print(f"  ❌ Error generating ETF {fund_code} configuration: {e}")
                
        # Perform a final strict validation
        self.verify_config()
        
        if skipped_hk_etfs or skipped_non_a_etfs:
            YELLOW = "\033[1;33m"
            CYAN = "\033[1;36m"
            BOLD = "\033[1m"
            RESET = "\033[0m"
            print("\n" + YELLOW + "======================================================================" + RESET)
            print(YELLOW + BOLD + "  🚫 [Exclusion] The following ETFs contain non-A-share components and were skipped:" + RESET)
            print(YELLOW + "======================================================================" + RESET)
            if skipped_hk_etfs:
                for fund, hks in skipped_hk_etfs.items():
                    print(YELLOW + BOLD + f"  👉 ETF [{fund}] contains {len(hks)} HK stocks:" + RESET)
                    for i in range(0, len(hks), 10):
                        print(CYAN + f"       {', '.join(hks[i:i+10])}" + RESET)
            if skipped_non_a_etfs:
                for fund, nas in skipped_non_a_etfs.items():
                    print(YELLOW + BOLD + f"  👉 ETF [{fund}] contains {len(nas)} non-A-shares (bonds/gold/commodities etc.):" + RESET)
                    for i in range(0, len(nas), 10):
                        print(CYAN + f"       {', '.join(nas[i:i+10])}" + RESET)
            print(YELLOW + "======================================================================\n" + RESET)

        if getattr(self, 'pcf_fetch_failures', None):
            YELLOW = "\033[1;33m"
            RED = "\033[1;31m"
            RESET = "\033[0m"
            BOLD = "\033[1m"
            print("\n" + RED + "🚨" * 35 + RESET)
            print(RED + "🚨" + " " * 10 + BOLD + "【 WARNING: FAILED TO FETCH SOME PCF DATA 】" + " " * 10 + "🚨" + RESET)
            print(RED + "🚨" * 35 + RESET)
            for fund, reason in self.pcf_fetch_failures:
                print(YELLOW + f"  👉 Fund [{fund}]: {reason}" + RESET)
            print(RED + "🚨" * 35 + "\n" + RESET)
