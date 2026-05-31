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

# 强制 stdout/stderr 使用 utf-8 编码以防 Windows 终端在打印中文字符或 Emoji 时报错
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
        # 单例模式
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
        # PCF 数据提供者（共享 pcf_fetch_failures 列表）
        self._sse_provider = SsePcfProvider(pcf_fetch_failures=self.pcf_fetch_failures)
        self._szse_provider = SzsePcfProvider(pcf_fetch_failures=self.pcf_fetch_failures)
        self.yesterday_date = yesterday_date
        
        # 默认配置文件路径
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.default_config_path = os.path.join(project_root, "files", "alphacore_config.json")

    def _ensure_history_data(self, stock_list, period, start_time, end_time, max_retries=5):
        """同步下载器，确保所有股票的数据在本地缓存"""
        missing_stocks = set(stock_list)
        print(f"  ⏬ 开始校验并拉取 {len(missing_stocks)} 只标的的 {period} 行情...")
        
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
                print("  ✅ 所有标的历史数据已 100% 落盘就绪！")
                missing_stocks = set()
                break
                
            missing_stocks = still_missing
            print(f"  ⏳ 第 {attempt + 1} 次校验: 尚有 {len(missing_stocks)} 只标的未就绪，正在拉取...")
            
            def dummy_cb(data):
                pass
                
            try:
                xtdata.download_history_data2(list(missing_stocks), period, start_time, end_time, dummy_cb)
            except Exception as e:
                print(f"  ❌ 调用批量下载失败: {e}")
                
            time.sleep(3.0)
            
        if missing_stocks:
            print(f"  ⚠️ 警告: 有 {len(missing_stocks)} 只股票(如 {list(missing_stocks)[:5]}) 无法获取数据，可能退市或停牌！")

    def _get_pcf_provider(self, fund_code: str) -> PcfProvider:
        """根据基金代码获取对应交易所的 PCF 数据提供者"""
        if fund_code.startswith("1"):
            return self._szse_provider
        return self._sse_provider

    def get_pcf_basic_info(self, fund_code: str) -> dict | None:
        """获取 ETF 申赎清单基本信息（现金差额、最小申赎单位、净值等，支持沪深两市）"""
        return self._get_pcf_provider(fund_code).get_basic_info(fund_code)

    def get_pcf_components(self, fund_code: str) -> pd.DataFrame | None:
        """获取 ETF 申赎清单成分股列表（支持沪深两市）"""
        return self._get_pcf_provider(fund_code).get_components(fund_code)

    def generate_config(self, fund_code: str, current_idx: int = 0, total_count: int = 0):
        """
        从 PCF 申赎清单生成 alphacore_config.json 配置
        """
        print("=" * 70)
        progress_str = f" 【{current_idx}/{total_count}】" if total_count > 0 else ""
        print(f"  生成 {fund_code} alphacore 配置{progress_str}")
        print("=" * 70)

        # ---- 1. 获取 PCF 成分股 ----
        print("\n[1/4] 获取 PCF 申赎清单...")
        if fund_code in self._pcf_info_cache:
            info = self._pcf_info_cache[fund_code]
        else:
            info = self.get_pcf_basic_info(fund_code)
            
        if fund_code in self._pcf_comp_cache:
            comp_df = self._pcf_comp_cache[fund_code]
        else:
            comp_df = self.get_pcf_components(fund_code)

        if comp_df is None or comp_df.empty:
            print("  ❌ 获取 PCF 成分股失败，终止")
            return

        # 筛选物理股票：排除 SUBSTITUTION_FLAG="2"（必须现金替代）
        physical_df = comp_df[comp_df["SUBSTITUTION_FLAG"] != "2"].copy()
        cash_only_df = comp_df[comp_df["SUBSTITUTION_FLAG"] == "2"]
        print(f"  成分股总数: {len(comp_df)}")
        print(f"  物理股票: {len(physical_df)}  (现金替代=允许)")
        print(f"  现金替代: {len(cash_only_df)}  (现金替代=必须，已排除)")

        components = {}
        hk_stocks = []
        non_a_shares = []
        for _, row in physical_df.iterrows():
            raw_code = str(row["INSTRUMENT_ID"]).strip().split('.')[0]
            market_id = str(row.get("UNDERLYION_SECURITY_ID", "")).strip()

            # 103 表示港股市场
            if market_id == "103" or (len(raw_code) == 5 and raw_code.isdigit()):
                # 统一转成 5 位港股代码
                code = raw_code.zfill(5)
                suffix = ".HK"
                hk_stocks.append(f"{code}{suffix}")
                stock_code = f"{code}{suffix}"
            else:
                # 严格判断是否为纯正 A 股，不要盲目使用 zfill(6) 以免把 981 变成 000981 误认为深交所 A 股
                # 如果真的是 A 股，交易所返回的一定是足额 6 位的字符串（如 '000001'）
                if not utils.is_normal_a_share(raw_code):
                    stock_code = raw_code
                    non_a_shares.append(stock_code)
                else:
                    stock_code = utils.enhance_stock_code(raw_code)
                    
            quantity = int(row["QUANTITY"])
            components[stock_code] = quantity

        print(f"  components 构建完成: {len(components)} 只股票")
        
        if not components:
            print("  🚫 该 ETF 无有效的 A 股物理成分股 (可能为全现金替代或境外 ETF)，跳过生成。")
            return {"skipped": True, "reason": "无有效A股物理成分股"}

        if hk_stocks or non_a_shares:
            skip_reasons = []
            if hk_stocks:
                skip_reasons.append(f"{len(hk_stocks)} 只港股成分股")
            if non_a_shares:
                skip_reasons.append(f"{len(non_a_shares)} 只非A股(债券/黄金/商品等)")
                
            print(f"  🚫 发现 {'、'.join(skip_reasons)}，当前配置系统暂不生成该 ETF。")
            return {"skipped": True, "hk_stocks": hk_stocks, "non_a_shares": non_a_shares}

        # ---- 2. 获取昨收价，计算 basket_pre_close ----
        print("\n[2/4] 获取昨收价并计算 basket_pre_close...")
        basket_pre_close = 0.0
        missing_count = 0
        stock_list = list(components.keys())
        market_data = {}
        
        qmt_yesterday = self.yesterday_date.replace('-', '')
        
        # 为了提高效率，我们分两阶段获取数据：
        # 第一阶段：先全部只拉取昨天的 1d 数据
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
            print(f"  ❌ 第一阶段调用 QMT xtdata.get_market_data_ex 失败: {e}")

        missing_stocks_info = []
        stock_last_close = {}

        # 尝试提取第一阶段的收盘价
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

        # 第二阶段：如果仍有股票没有昨收价（大概率为停牌），则针对这些股票拉取 60 天的数据回溯
        if missing_stocks_info:
            print(f"  ⏳ 针对 {len(missing_stocks_info)} 只停牌或无数据股票启动 60 天深度回溯...")
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
                
                # 重新提取这部分股票的价格
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
                print(f"  ❌ 第二阶段调用 QMT xtdata.get_market_data_ex 失败: {e}")

        # 汇总最终的价格进行累加
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
            print(f"  ⚠ 有 {missing_count} 只股票未找到昨收价 (例如: {', '.join(final_missing_stocks[:5])})")
        basket_pre_close = round(basket_pre_close, 2)
        print(f"  basket_pre_close = {basket_pre_close}")

        # ---- 3. 获取指数昨收 ----
        print("\n[3/4] 获取指数昨收...")
        index_code = 0
        index_pre_close = 0.0
        if self.etf_target_index_map and fund_code in self.etf_target_index_map:
            index_data = self.etf_target_index_map[fund_code]
            index_code = index_data["index_code"]
            index_pre_close = index_data["index_pre_price"]
        
        if index_pre_close > 0:
            print(f"  指数 {index_code} 昨收: {index_pre_close}")
        else:
            print(" ❌️ ⚠ 未能获取指数昨收，使用 0.0 占位")

        # ---- 4. 写入配置文件 ----
        print("\n[4/4] 写入配置文件...")
        config_path = self.default_config_path

        estimated_cash = PcfProvider.clean_float(info.get("ESTIMATED_CASH_COMPONENT", 0.0)) if info else 0.0
        net_asset_value = PcfProvider.clean_float(info.get("NAV", 0.0)) if info else 0.0

        # 提取 PCF 中的原始金额
        origin_basket_amount_raw = info.get("NAVPERCU") or info.get("最小申购、赎回单位资产净值") or 0.0
        origin_basket_amount = PcfProvider.clean_float(origin_basket_amount_raw)
        
        # 当天日期
        today_str = datetime.datetime.now().strftime("%Y%m%d")

        # 增量读取并覆盖写入配置文件
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

        # 确保所在目录存在
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)

        print(f"  配置已保存到: {config_path}")
        print(f"\n  --- 配置摘要 ---")
        print(f"  index_code:           {index_code}")
        print(f"  index_pre_close:      {index_pre_close}")
        print(f"  basket_pre_close:     {basket_pre_close}")
        print(f"  estimated_cash:       {estimated_cash}")
        print(f"  net_asset_value:      {net_asset_value}")
        print(f"  origin_basket_amount: {origin_basket_amount}")
        print(f"  hidden_substitute_amount:{origin_basket_amount-estimated_cash-basket_pre_close}")
        print(f"  update_date:          {today_str}")
        print(f"  components:           {len(components)} 只物理股票")

        print("\n" + "=" * 70)
        print("  完成")
        print("=" * 70)
        
        return {"skipped": False}

    def verify_config(self):
        print("\n" + "=" * 70)
        print("  开始配置文件校验...")
        print("=" * 70)
        
        if not os.path.exists(self.default_config_path):
            print(f"  ❌ 校验失败: 配置文件 {self.default_config_path} 不存在")
            return

        try:
            with open(self.default_config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as e:
            print(f"  ❌ 校验失败: 读取配置文件时出错: {e}")
            return

        today_str = datetime.datetime.now().strftime("%Y%m%d")
        outdated_funds = []


        for fund_code, data in config_data.items():
            # 校验 update_date
            update_date = data.get("update_date", "")
            if update_date != today_str:
                outdated_funds.append((fund_code, update_date))

        # 输出校验结果
        has_error = len(outdated_funds) > 0
        # ANSI escape sequences for bold, colored terminal logs
        RED = "\033[1;31m"
        GREEN = "\033[1;32m"
        YELLOW = "\033[1;33m"
        CYAN = "\033[1;36m"
        BOLD = "\033[1m"
        RESET = "\033[0m"

        if has_error:
            # 醒目的红色警告大框框
            print("\n" + RED + "🚨" * 40 + RESET)
            print(RED + "🚨" + " " * 27 + BOLD + "【 配置文件校验失败！ 】" + " " * 27 + "🚨" + RESET)
            print(RED + "🚨" * 40 + RESET)
            
            if outdated_funds:
                print(YELLOW + BOLD + "\n  👉【 日期未更新异常列表 】" + RESET)
                for fund_code, u_date in outdated_funds:
                    if not u_date:
                        print(RED + BOLD + f"    ✗ 基金 [{fund_code}]: 缺失 update_date 字段，请检查生成逻辑！" + RESET)
                    else:
                        print(RED + BOLD + f"    ✗ 基金 [{fund_code}]: 日期为 {u_date} (今天应为 {today_str}) 【漏掉未更新！】" + RESET)
            print("\n" + RED + "🚨" * 40 + RESET)
        else:
            print("\n" + GREEN + "======================================================================" + RESET)
            print(GREEN + BOLD + "  ✓ 配置文件校验通过！所有基金日期均为当天！" + RESET)
            print(GREEN + "======================================================================" + RESET)

    def run(self):
        self.pcf_fetch_failures = []
        # 同步更新 Provider 的错误列表引用
        self._sse_provider.pcf_fetch_failures = self.pcf_fetch_failures
        self._szse_provider.pcf_fetch_failures = self.pcf_fetch_failures
        fund_codes = []

        stocks = stock_db.get_stock_list(self.db, 'etf')
        for stock in stocks:
            code = stock['code']
            # 目前只跑上交所
            # if code.startswith('1'):
            #     fund_codes.append(code)
            
            # 目前只允许上交所(5开头)进入配置生成流水线
            if code.startswith('5'):
                fund_codes.append(code)

        # 临时具体看看什么情况
        # fund_codes = ["515530"]

        self.etf_target_index_map = index_daily_history.get_etf_target_index_pre_close(self.db, fund_codes, self.yesterday_date)
        if not fund_codes:
            print("  ⚠ 未提供任何 ETF 代码，跳过生成")
            return
            
        print("\n" + "=" * 70)
        print(f"  [预处理] 汇总所有 ETF 成分股并进行预下载...")
        print("=" * 70)
        
        all_required_stocks = set()
        self._pcf_info_cache = {}
        self._pcf_comp_cache = {}
        
        total_funds = len(fund_codes)
        for i, fund_code in enumerate(fund_codes):
            print(f"  ⏳ [{i+1}/{total_funds}] 正在获取并解析 ETF {fund_code} 的 PCF 清单...")
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
            print(f"  ⏬ 共提取到 {len(all_required_stocks)} 只唯一标的，开始批量预下载 {qmt_yesterday} 数据...")
            def dummy_cb(data):
                pass
            try:
                xtdata.download_history_data2(list(all_required_stocks), '1d', qmt_yesterday, qmt_yesterday, dummy_cb)
                print(f"  ✅ 预下载完成！等待 5 秒落盘...")
                time.sleep(5.0)
            except Exception as e:
                print(f"  ❌ 批量预下载调用失败: {e}")

        print("\n" + "=" * 70)
        print(f"  开始批量生成 ETF 配置，共计 {len(fund_codes)} 个基金")
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
                print(f"  ❌ 生成 ETF {fund_code} 配置时发生错误: {e}")
                
        # 统一执行一次强校验
        self.verify_config()
        
        if skipped_hk_etfs or skipped_non_a_etfs:
            YELLOW = "\033[1;33m"
            CYAN = "\033[1;36m"
            BOLD = "\033[1m"
            RESET = "\033[0m"
            print("\n" + YELLOW + "======================================================================" + RESET)
            print(YELLOW + BOLD + "  🚫 【特殊情况】以下 ETF 包含非A股成分，已跳过配置生成：" + RESET)
            print(YELLOW + "======================================================================" + RESET)
            if skipped_hk_etfs:
                for fund, hks in skipped_hk_etfs.items():
                    print(YELLOW + BOLD + f"  👉 ETF [{fund}] 包含 {len(hks)} 只港股：" + RESET)
                    for i in range(0, len(hks), 10):
                        print(CYAN + f"       {', '.join(hks[i:i+10])}" + RESET)
            if skipped_non_a_etfs:
                for fund, nas in skipped_non_a_etfs.items():
                    print(YELLOW + BOLD + f"  👉 ETF [{fund}] 包含 {len(nas)} 只非A股(债券/黄金/商品等)：" + RESET)
                    for i in range(0, len(nas), 10):
                        print(CYAN + f"       {', '.join(nas[i:i+10])}" + RESET)
            print(YELLOW + "======================================================================\n" + RESET)

        if getattr(self, 'pcf_fetch_failures', None):
            YELLOW = "\033[1;33m"
            RED = "\033[1;31m"
            RESET = "\033[0m"
            BOLD = "\033[1m"
            print("\n" + RED + "🚨" * 35 + RESET)
            print(RED + "🚨" + " " * 15 + BOLD + "【 人工介入警告：部分 PCF 拉取失败 】" + " " * 16 + "🚨" + RESET)
            print(RED + "🚨" * 35 + RESET)
            for fund, reason in self.pcf_fetch_failures:
                print(YELLOW + f"  👉 基金 [{fund}] : {reason}" + RESET)
            print(RED + "🚨" * 35 + "\n" + RESET)

