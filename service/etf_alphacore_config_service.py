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
import time
from db import index_daily_history
from db import stock as stock_db
from helper import utils

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
        self._szse_pcf_cache = {}
        self._pcf_info_cache = {}
        self._pcf_comp_cache = {}
        self.pcf_fetch_failures = []
        self.db = db
        self.yesterday_date = yesterday_date
        
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
        today_str = now.strftime("%Y%m%d")
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pcf_dir = os.path.join(project_root, "files", "pcf", today_str)
        os.makedirs(pcf_dir, exist_ok=True)
        
        content = None
        success_date_str = None
        
        # 1. 尝试从本地缓存读取
        import glob
        cache_files = glob.glob(os.path.join(pcf_dir, f"SZSE_{fund_code}_*.txt"))
        if cache_files:
            cache_file = cache_files[0]
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    content = f.read()
                filename = os.path.basename(cache_file)
                success_date_str = filename.replace(f"SZSE_{fund_code}_", "").replace(".txt", "")
                print(f"  📖 [缓存读取] 成功读取本地深交所 ETF {fund_code} {success_date_str} 申赎清单数据")
            except Exception as e:
                print(f"  ❌ 读取本地缓存失败: {e}")
                content = None
                success_date_str = None

        # 2. 如果没有缓存，则从网络获取并写入缓存
        if not content:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            for i in range(5):
                date_str = (now - datetime.timedelta(days=i)).strftime("%Y%m%d")
                url = f"https://reportdocs.static.szse.cn/files/text/etf/ETF{fund_code}{date_str}.txt?random={random.random()}"
                try:
                    resp = requests.get(url, headers=headers, timeout=10)
                    if resp.status_code == 200:
                        encoding = resp.apparent_encoding or 'gbk'
                        content = resp.content.decode(encoding, errors='ignore')
                        success_date_str = date_str
                        print(f"  🌐 [网络拉取] 成功获取深交所 ETF {fund_code} {date_str} 申赎清单数据")

                        # 存入本地文件
                        cache_file = os.path.join(pcf_dir, f"SZSE_{fund_code}_{success_date_str}.txt")
                        try:
                            with open(cache_file, "w", encoding="utf-8") as f:
                                f.write(content)
                        except Exception as e:
                            print(f"  ❌ 写入本地缓存失败: {e}")
                        break
                except Exception:
                    continue
                
        if not content:
            print(f"获取深交所 ETF {fund_code} 申赎清单数据失败")
            self.pcf_fetch_failures.append((fund_code, "深交所PCF清单数据拉取失败（连续5天尝试均无数据或网络异常）"))
            return None
            
        metadata, components = self.parse_szse_etf_txt(content)
        if success_date_str:
            metadata['TRADING_DAY'] = success_date_str
        self._szse_pcf_cache[fund_code] = (metadata, components)
        return metadata, components

    def get_sse_pcf_basic_info(self, fund_code: str) -> dict | None:
        """获取上交所 ETF 申赎清单基本信息"""
        today_str = datetime.datetime.now().strftime("%Y%m%d")
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pcf_dir = os.path.join(project_root, "files", "pcf", today_str)
        os.makedirs(pcf_dir, exist_ok=True)
        cache_file = os.path.join(pcf_dir, f"SSE_{fund_code}_basic.json")
        
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    print(f"  📖 [缓存读取] 成功读取本地上交所 ETF {fund_code} 基本信息")
                    return json.load(f)
            except Exception:
                pass
                
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
                result_data = data["result"][0]
                print(f"  🌐 [网络拉取] 成功获取上交所 ETF {fund_code} 基本信息")
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(result_data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                return result_data
            self.pcf_fetch_failures.append((fund_code, "上交所PCF基本信息接口返回空数据"))
            return None
        except Exception as e:
            print(f"获取上交所PCF基本信息失败: {e}")
            self.pcf_fetch_failures.append((fund_code, f"上交所PCF基本信息请求异常: {e}"))
            return None

    def get_sse_pcf_components(self, fund_code: str) -> pd.DataFrame | None:
        """获取上交所 ETF 申赎清单成分股列表"""
        today_str = datetime.datetime.now().strftime("%Y%m%d")
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pcf_dir = os.path.join(project_root, "files", "pcf", today_str)
        os.makedirs(pcf_dir, exist_ok=True)
        cache_file = os.path.join(pcf_dir, f"SSE_{fund_code}_comp.json")
        
        result_list = None
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    result_list = json.load(f)
                    print(f"  📖 [缓存读取] 成功读取本地上交所 ETF {fund_code} 成分股")
            except Exception:
                pass
                
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
        
        if result_list is None:
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                if data and data.get("result") and len(data["result"]) > 0:
                    result_list = data["result"]
                    print(f"  🌐 [网络拉取] 成功获取上交所 ETF {fund_code} 成分股")
                    try:
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump(result_list, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
                else:
                    self.pcf_fetch_failures.append((fund_code, "上交所PCF成分股接口返回空数据"))
                    return None
            except Exception as e:
                print(f"获取上交所PCF成分股失败: {e}")
                self.pcf_fetch_failures.append((fund_code, f"上交所PCF成分股请求异常: {e}"))
                return None
                
        try:
            df = pd.DataFrame(result_list)
            
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
        except Exception as e:
            print(f"解析上交所PCF成分股失败: {e}")
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

            # 申购替代金额过滤掉没有用
            if code == "159900" and qty_str == "0":
                continue
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
        for _, row in physical_df.iterrows():
            raw_code = str(row["INSTRUMENT_ID"]).strip().split('.')[0]

            # 港股处理：如果原代码刚好是 5 位数字，说明是港股通标的
            if len(raw_code) == 5 and raw_code.isdigit():
                code = raw_code
                suffix = ".HK"
                hk_stocks.append(f"{code}{suffix}")
                stock_code = f"{code}{suffix}"
            else:
                stock_code = utils.enhance_stock_code(raw_code.zfill(6))
            quantity = int(row["QUANTITY"])
            components[stock_code] = quantity

        print(f"  components 构建完成: {len(components)} 只股票")

        if hk_stocks:
            print(f"  🚫 发现 {len(hk_stocks)} 只港股成分股，当前配置系统暂不生成该 ETF。")
            return {"skipped": True, "hk_stocks": hk_stocks}

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

    def run(self):
        self.pcf_fetch_failures = []
        fund_codes = []

        stocks = stock_db.get_stock_list(self.db, 'etf')
        for stock in stocks:
            fund_codes.append(stock['code'])

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
        
        for fund_code in fund_codes:
            info = self.get_pcf_basic_info(fund_code)
            comp_df = self.get_pcf_components(fund_code)
            self._pcf_info_cache[fund_code] = info
            self._pcf_comp_cache[fund_code] = comp_df
            
            if comp_df is not None and not comp_df.empty:
                physical_df = comp_df[comp_df["SUBSTITUTION_FLAG"] != "2"]
                for _, row in physical_df.iterrows():
                    raw_code = str(row["INSTRUMENT_ID"]).strip().split('.')[0]
                    if len(raw_code) == 5 and raw_code.isdigit():
                        continue
                    else:
                        code = raw_code.zfill(6)
                        all_required_stocks.add(utils.enhance_stock_code(code))

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
        total_funds = len(fund_codes)
        for i, fund_code in enumerate(fund_codes):
            try:
                res = self.generate_config(fund_code, current_idx=i+1, total_count=total_funds)
                if res and res.get("skipped"):
                    skipped_hk_etfs[fund_code] = res.get("hk_stocks", [])
            except Exception as e:
                print(f"  ❌ 生成 ETF {fund_code} 配置时发生错误: {e}")
                
        # 统一执行一次强校验
        self.verify_config()
        
        if skipped_hk_etfs:
            YELLOW = "\033[1;33m"
            CYAN = "\033[1;36m"
            BOLD = "\033[1m"
            RESET = "\033[0m"
            print("\n" + YELLOW + "======================================================================" + RESET)
            print(YELLOW + BOLD + "  🚫 【特殊情况】以下 ETF 包含港股成分，已跳过配置生成：" + RESET)
            print(YELLOW + "======================================================================" + RESET)
            for fund, hks in skipped_hk_etfs.items():
                print(YELLOW + BOLD + f"  👉 ETF [{fund}] 包含 {len(hks)} 只港股：" + RESET)
                for i in range(0, len(hks), 10):
                    print(CYAN + f"       {', '.join(hks[i:i+10])}" + RESET)
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

