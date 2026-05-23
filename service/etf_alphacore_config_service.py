# service/etf_alphacore_config_service.py
import json
import os
import sys
import datetime
import random
import threading
import pandas as pd
import requests
from xtquant import xtdata

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

    def __init__(self, db):
        # 防止重复初始化
        if hasattr(self, 'initialized'):
            return
        self.initialized = True
        self._szse_pcf_cache = {}
        self.db = db
        
        # 默认配置文件路径
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.default_config_path = os.path.join(project_root, "files", "alphacore_config.json")

    def clean_float(self, val) -> float:
        """清洗并解析浮点数字符串，兼容清除 ￥、元、逗号千分符等货币单位"""
        if not val:
            return 0.0
        val = str(val).replace('￥', '').replace('元', '').replace(',', '').strip()
        try:
            return float(val)
        except ValueError:
            return 0.0

    def get_market_by_stock_code(self, code: str) -> str:
        """根据证券代码前缀自动识别其所属市场 ID"""
        if code.startswith(('60', '68', '90')):
            return "101"  # 上海市场 (.SH)
        elif code.startswith(('00', '30', '20')):
            return "102"  # 深圳市场 (.SZ)
        elif code.startswith(('43', '83', '87', '88')):
            return "106"  # 北京市场 (.BJ)
        return "101"  # 默认上海

    def parse_szse_etf_txt(self, content: str) -> tuple[dict, list]:
        """
        解析深交所 ETF PCF 文本文件内容。
        支持解析基础元数据和成份股列表。
        """
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        metadata = {}
        components = []
        
        col_mappings = {
            '证券代码': 'code',
            '证券简称': 'name',
            '股份数量': 'quantity',
            '数量': 'quantity',
            '现金替代标志': 'sub_flag',
            '替代标志': 'sub_flag',
            '溢价比例': 'premium_ratio',
            '替代溢价比例': 'premium_ratio',
            '代替金额': 'sub_amount',
            '固定替代金额': 'sub_amount',
            '替代金额': 'sub_amount'
        }
        
        headers = None
        
        for line in lines:
            if '=' in line and not line.startswith('='):
                parts = line.split('=', 1)
                metadata[parts[0].strip()] = parts[1].strip()
                continue
            elif ':' in line and not line.startswith(':'):
                parts = line.split(':', 1)
                if len(parts[0].strip()) < 30 and not parts[1].strip().startswith('//'):
                    metadata[parts[0].strip()] = parts[1].strip()
                    continue
            elif '：' in line:
                parts = line.split('：', 1)
                if len(parts[0].strip()) < 30:
                    metadata[parts[0].strip()] = parts[1].strip()
                    continue
                    
            parts = [p.strip() for p in line.split('\t') if p.strip()]
            if len(parts) <= 1:
                parts = [p.strip() for p in line.split('  ') if p.strip()]
            if len(parts) <= 1:
                parts = [p.strip() for p in line.split() if p.strip()]
                
            if len(parts) > 1:
                if any(col in parts for col in col_mappings.keys()):
                    headers = parts
                    continue
                
                if headers:
                    row_data = {}
                    for idx, part in enumerate(parts):
                        if idx < len(headers):
                            col_name = headers[idx]
                            field_name = col_mappings.get(col_name, col_name)
                            row_data[field_name] = part
                    components.append(row_data)
                else:
                    if parts[0].isdigit() and len(parts[0]) == 6:
                        components.append({
                            'code': parts[0],
                            'name': parts[1] if len(parts) > 1 else '',
                            'quantity': parts[2] if len(parts) > 2 else '',
                            'sub_flag': parts[3] if len(parts) > 3 else '',
                            'raw_line': line
                        })
                        
        if not components:
            for line in lines:
                parts = line.split()
                if parts and parts[0].isdigit() and len(parts[0]) == 6:
                    row_data = {'code': parts[0]}
                    if len(parts) > 1: row_data['name'] = parts[1]
                    if len(parts) > 2: row_data['quantity'] = parts[2]
                    if len(parts) > 3: row_data['sub_flag'] = parts[3]
                    components.append(row_data)
                    
        return metadata, components

    def _load_szse_pcf(self, fund_code: str) -> tuple[dict, list] | None:
        """动态获取并解析深交所 ETF PCF 清单数据（自动向前回溯 5 天以兼容节假日/非交易时间）"""
        if fund_code in self._szse_pcf_cache:
            return self._szse_pcf_cache[fund_code]
            
        now = datetime.datetime.now()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        content = None
        success_date_str = None
        for i in range(5):
            date_str = (now - datetime.timedelta(days=i)).strftime("%Y%m%d")
            url = f"https://reportdocs.static.szse.cn/files/text/etf/ETF{fund_code}{date_str}.txt?random={random.random()}"
            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    encoding = resp.apparent_encoding or 'gbk'
                    content = resp.content.decode(encoding, errors='ignore')
                    success_date_str = date_str
                    print(f"成功获取深交所 ETF {fund_code} {date_str} 申赎清单数据")
                    break
            except Exception:
                continue
                
        if not content:
            print(f"获取深交所 ETF {fund_code} 申赎清单数据失败")
            return None
            
        metadata, components = self.parse_szse_etf_txt(content)
        if success_date_str:
            metadata['TRADING_DAY'] = success_date_str
        self._szse_pcf_cache[fund_code] = (metadata, components)
        return metadata, components

    def get_sse_pcf_basic_info(self, fund_code: str) -> dict | None:
        """获取上交所 ETF 申赎清单基本信息"""
        url = "https://query.sse.com.cn/commonQuery.do"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.sse.com.cn/disclosure/fund/etflist/detail.shtml",
        }
        params = {
            "isPagination": "false",
            "FUNDID2": fund_code,
            "sqlId": "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C",
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data and data.get("result") and len(data["result"]) > 0:
                return data["result"][0]
            return None
        except Exception as e:
            print(f"获取上交所PCF基本信息失败: {e}")
            return None

    def get_sse_pcf_components(self, fund_code: str) -> pd.DataFrame | None:
        """获取上交所 ETF 申赎清单成分股列表"""
        url = "https://query.sse.com.cn/commonQuery.do"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.sse.com.cn/disclosure/fund/etflist/detail.shtml",
        }
        params = {
            "isPagination": "false",
            "FUNDID2": fund_code,
            "sqlId": "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_COMPONENT_C",
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data and data.get("result") and len(data["result"]) > 0:
                df = pd.DataFrame(data["result"])
                
                # 清洗上交所特定的申购替代金额 (SUBSTITUTION_CASH_AMOUNT) 并映射为 '申购替代金额' 列
                def clean_sse_sub_amount(val):
                    if not val or str(val).strip() == '-':
                        return '0'
                    return str(val).replace(',', '').strip()
                    
                if 'SUBSTITUTION_CASH_AMOUNT' in df.columns:
                    df['申购替代金额'] = df['SUBSTITUTION_CASH_AMOUNT'].apply(clean_sse_sub_amount)
                else:
                    df['申购替代金额'] = '0'
                    
                return df
            return None
        except Exception as e:
            print(f"获取上交所PCF成分股失败: {e}")
            return None

    def get_szse_pcf_basic_info(self, fund_code: str) -> dict | None:
        """获取深交所 ETF 申赎清单基本信息，并映射为与上交所一致的字段"""
        res = self._load_szse_pcf(fund_code)
        if not res:
            return None
        metadata, _ = res
        mapped_info = {
            "ESTIMATED_CASH_COMPONENT": metadata.get("预估现金差额", "0.0"),
            "NAV": metadata.get("基金份额净值", "0.0")
        }
        mapped_info.update(metadata)
        return mapped_info

    def get_szse_pcf_components(self, fund_code: str) -> pd.DataFrame | None:
        """获取深交所 ETF 申赎清单成分股列表，并映射为与上交所一致的格式"""
        res = self._load_szse_pcf(fund_code)
        if not res:
            return None
        _, components = res
        if not components:
            return None
            
        mapped_list = []
        for item in components:
            code = item.get("code", "")
            qty_str = str(item.get("quantity", "0")).replace(",", "").strip()
            try:
                qty = int(float(qty_str))
            except ValueError:
                qty = 0
                
            sub_flag_str = item.get("sub_flag", "允许")
            if sub_flag_str == "必须" or sub_flag_str == "2":
                sub_flag = "2"
            elif sub_flag_str == "允许" or sub_flag_str == "1":
                sub_flag = "1"
            else:
                sub_flag = "0"
                
            market = self.get_market_by_stock_code(code)
            
            mapped_item = {
                "INSTRUMENT_ID": code,
                "UNDERLYION_SECURITY_ID": market,
                "QUANTITY": qty,
                "SUBSTITUTION_FLAG": sub_flag,
                "申购替代金额": str(item.get("申购替代金额", "0")).replace(",", "").strip(),
            }
            mapped_list.append(mapped_item)
            
        return pd.DataFrame(mapped_list)

    def get_pcf_basic_info(self, fund_code: str) -> dict | None:
        """获取 ETF 申赎清单基本信息（现金差额、最小申赎单位、净值等，支持沪深两市）"""
        if fund_code.startswith("1"):
            return self.get_szse_pcf_basic_info(fund_code)
        else:
            return self.get_sse_pcf_basic_info(fund_code)

    def get_pcf_components(self, fund_code: str) -> pd.DataFrame | None:
        """获取 ETF 申赎清单成分股列表（支持沪深两市）"""
        if fund_code.startswith("1"):
            return self.get_szse_pcf_components(fund_code)
        else:
            return self.get_sse_pcf_components(fund_code)

    def generate_config(self, fund_code: str, config_path: str = None):
        """
        从 PCF 申赎清单生成 alphacore_config.json 配置
        """
        print("=" * 70)
        print(f"  生成 {fund_code} alphacore 配置")
        print("=" * 70)

        # ---- 1. 获取 PCF 成分股 ----
        print("\n[1/4] 获取 PCF 申赎清单...")
        info = self.get_pcf_basic_info(fund_code)
        print(info)
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

        # 构建 components 字典: { "600006.SH": 900, ... }
        market_suffix = {"101": ".SH", "102": ".SZ", "106": ".BJ"}
        components = {}
        for _, row in physical_df.iterrows():
            code = str(row["INSTRUMENT_ID"])
            market = str(row.get("UNDERLYION_SECURITY_ID", "101"))
            suffix = market_suffix.get(market, ".SH")
            stock_code = f"{code}{suffix}"
            quantity = int(row["QUANTITY"])
            components[stock_code] = quantity

        print(f"  components 构建完成: {len(components)} 只股票")

        # ---- 2. 获取昨收价，计算 basket_pre_close ----
        print("\n[2/4] 获取昨收价并计算 basket_pre_close...")
        basket_pre_close = 0.0
        missing_count = 0
        stock_list = list(components.keys())
        # 触发 QMT 异步下载所有成分股 of the K-lines
        try:
            print(f"  正在请求 QMT 异步下载 {len(stock_list)} 只成分股的日 K 线历史数据...")
            # 扩展下载历史天数至 60 天，确保停牌时间较长时也能覆盖到
            start_date = (datetime.datetime.now() - datetime.timedelta(days=60)).strftime("%Y%m%d")
            xtdata.download_history_data2(stock_list=stock_list, period='1d', start_time=start_date)
        except Exception as e:
            print(f"  ⚠ 触发历史数据下载失败: {e}")

        close_df = None
        try:
            # 使用 count=30 调取过去 30 个交易日数据，以便在停牌时能够往前找最后一次的收盘价
            market_data = xtdata.get_market_data(field_list=['close'], stock_list=stock_list, period='1d', count=30)
            if market_data and 'close' in market_data:
                close_df = market_data['close']
        except Exception as e:
            print(f"  ❌ 调用 QMT xtdata.get_market_data 失败: {e}")

        for stock_code, qty in components.items():
            last_close = 0.0
            if close_df is not None and stock_code in close_df.index:
                try:
                    row = close_df.loc[stock_code]
                    # 提取非空的最接近的值作为最近的收盘价
                    valid_closes = row.dropna()
                    if not valid_closes.empty:
                        last_close = float(valid_closes.iloc[-1])
                except Exception:
                    pass
                    
            if last_close > 0:
                basket_pre_close += last_close * qty
            else:
                missing_count += 1

        if missing_count > 0:
            print(f"  ⚠ 有 {missing_count} 只股票未找到昨收价")
        basket_pre_close = round(basket_pre_close, 2)
        print(f"  basket_pre_close = {basket_pre_close}")

        # ---- 3. 获取指数昨收 ----
        print("\n[3/4] 获取指数昨收...")
        index_pre_close = 0.0
        # 优先获取 ETF 的目标指数代码，若不存在则默认使用上证580指数 950580.SH
        index_code = info.get("目标指数代码", "950580") if info else "950580"
        if "." not in index_code:
            if index_code.startswith("399"):
                index_code = f"{index_code}.SZ"
            else:
                index_code = f"{index_code}.SH"
                
        print(f"  查询指数代码: {index_code}")
        
        # 触发 QMT 同步下载指数日线历史行情数据，确保本地缓存中存在昨收价
        try:
            # 扩展指数下载范围至 60 天
            start_date = (datetime.datetime.now() - datetime.timedelta(days=60)).strftime("%Y%m%d")
            xtdata.download_history_data(stock_code=index_code, period='1d', start_time=start_date)
        except Exception as e:
            print(f"  ⚠ 下载指数历史数据失败: {e}")
        
        if index_pre_close > 0:
            print(f"  指数 {index_code} 昨收: {index_pre_close}")
        else:
            print("  ⚠ 未能获取指数昨收，使用 0.0 占位")

        # ---- 4. 写入配置文件 ----
        print("\n[4/4] 写入配置文件...")
        if config_path is None:
            config_path = self.default_config_path

        estimated_cash = self.clean_float(info.get("ESTIMATED_CASH_COMPONENT", 0.0)) if info else 0.0
        net_asset_value = self.clean_float(info.get("NAV", 0.0)) if info else 0.0

        # 提取 PCF 中的原始金额
        origin_basket_amount_raw = info.get("NAVPERCU") or info.get("最小申购、赎回单位资产净值") or 0.0
        origin_basket_amount = self.clean_float(origin_basket_amount_raw)
        
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
            "index_pre_close": index_pre_close,
            "basket_pre_close": basket_pre_close,
            "estimated_cash": estimated_cash,
            "net_asset_value": net_asset_value,
            "origin_basket_amount": origin_basket_amount,
            "update_date": today_str,
            "components": components,
        }

        # 确保所在目录存在
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)

        print(f"  配置已保存到: {config_path}")
        print(f"\n  --- 配置摘要 ---")
        print(f"  index_pre_close:      {index_pre_close}")
        print(f"  basket_pre_close:     {basket_pre_close}")
        print(f"  estimated_cash:       {estimated_cash}")
        print(f"  net_asset_value:      {net_asset_value}")
        print(f"  origin_basket_amount: {origin_basket_amount}")
        print(f"  update_date:          {today_str}")
        print(f"  components:           {len(components)} 只物理股票")

        print("\n" + "=" * 70)
        print("  完成")
        print("=" * 70)

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
        mismatch_funds = []

        for fund_code, data in config_data.items():
            # 1. 校验 update_date
            update_date = data.get("update_date", "")
            if update_date != today_str:
                outdated_funds.append((fund_code, update_date))

            # 2. 校验 basket_pre_close + estimated_cash == origin_basket_amount
            basket_pre_close = data.get("basket_pre_close", 0.0)
            estimated_cash = data.get("estimated_cash", 0.0)
            origin_basket_amount = data.get("origin_basket_amount", 0.0)
            
            calculated_amount = round(basket_pre_close + estimated_cash, 2)
            # 允许 0.01 的浮点数误差
            if abs(calculated_amount - origin_basket_amount) > 0.01:
                diff = calculated_amount - origin_basket_amount
                mismatch_funds.append({
                    "fund_code": fund_code,
                    "calculated": calculated_amount,
                    "origin": origin_basket_amount,
                    "diff": round(diff, 2)
                })

        # 输出校验结果
        has_error = len(outdated_funds) > 0 or len(mismatch_funds) > 0
        
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
                        
            if mismatch_funds:
                print(YELLOW + BOLD + "\n  👉【 金额不一致异常列表 (basket_pre_close + 现金部分 != origin_basket_amount) 】" + RESET)
                for item in mismatch_funds:
                    print(RED + BOLD + f"    ✗ 基金 [{item['fund_code']}]:" + RESET)
                    print(f"      - 计算金额 (物理市值+现金): {CYAN}{item['calculated']:.2f}{RESET}")
                    print(f"      - PCF 原始金额 (origin):   {CYAN}{item['origin']:.2f}{RESET}")
                    print(f"      - 偏差金额 (计算 - 原始):   {RED}{BOLD}{item['diff']:+.2f}{RESET}")
                    print(f"      [注] 若此 ETF 包含必须现金替代成分(SUBSTITUTION_FLAG=2)，该偏差属正常现象。")
            print("\n" + RED + "🚨" * 40 + RESET)
        else:
            print("\n" + GREEN + "======================================================================" + RESET)
            print(GREEN + BOLD + "  ✓ 配置文件校验通过！所有基金日期均为当天，且计算总额与 PCF 原始金额完全一致！" + RESET)
            print(GREEN + "======================================================================" + RESET)

    def run(self, fund_codes: list[str]):
        """
        供外部调用的统一入口方法：循环执行每个 ETF 的配置生成，最后统一做一次强校验。
        :param fund_codes: ETF 代码列表，例如 ["530100", "159810"]
        :param config_path: 配置文件保存路径，若为 None 则使用 default_config_path
        """
        if not fund_codes:
            print("  ⚠ 未提供任何 ETF 代码，跳过生成")
            return
            
        print("\n" + "=" * 70)
        print(f"  开始批量生成 ETF 配置，共计 {len(fund_codes)} 个基金")
        print("=" * 70)
        
        for fund_code in fund_codes:
            try:
                self.generate_config(fund_code)
            except Exception as e:
                print(f"  ❌ 生成 ETF {fund_code} 配置时发生错误: {e}")
                
        # 统一执行一次强校验
        self.verify_config()

