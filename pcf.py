import json
import os
import sys
import datetime
import random
import pandas as pd
import requests

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

from xtquant import xtdata


# ============================================================
# 上交所 PCF 接口
# ============================================================
SSE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.sse.com.cn/disclosure/fund/etflist/detail.shtml",
}

def get_sse_pcf_basic_info(fund_code: str) -> dict | None:
    """获取上交所 ETF 申赎清单基本信息"""
    params = {
        "isPagination": "false",
        "FUNDID2": fund_code,
        "sqlId": "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C",
    }
    try:
        resp = requests.get(SSE_QUERY_URL, headers=SSE_HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data and data.get("result") and len(data["result"]) > 0:
            return data["result"][0]
        return None
    except Exception as e:
        print(f"获取上交所PCF基本信息失败: {e}")
        return None


def get_sse_pcf_components(fund_code: str) -> pd.DataFrame | None:
    """获取上交所 ETF 申赎清单成分股列表"""
    params = {
        "isPagination": "false",
        "FUNDID2": fund_code,
        "sqlId": "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_COMPONENT_C",
    }
    try:
        resp = requests.get(SSE_QUERY_URL, headers=SSE_HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data and data.get("result") and len(data["result"]) > 0:
            return pd.DataFrame(data["result"])
        return None
    except Exception as e:
        print(f"获取上交所PCF成分股失败: {e}")
        return None


# ============================================================
# 深交所 PCF 接口及解析逻辑
# ============================================================
_szse_pcf_cache = {}  # 用于缓存已下载并解析的深交所清单数据

def parse_szse_etf_txt(content: str):
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


def _load_szse_pcf(fund_code: str) -> tuple[dict, list] | None:
    """动态获取并解析深交所 ETF PCF 清单数据（自动向前回溯 5 天以兼容节假日/非交易时间）"""
    if fund_code in _szse_pcf_cache:
        return _szse_pcf_cache[fund_code]
        
    now = datetime.datetime.now()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    content = None
    for i in range(5):
        date_str = (now - datetime.timedelta(days=i)).strftime("%Y%m%d")
        url = f"https://reportdocs.static.szse.cn/files/text/etf/ETF{fund_code}{date_str}.txt?random={random.random()}"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                encoding = resp.apparent_encoding or 'gbk'
                content = resp.content.decode(encoding, errors='ignore')
                print(f"成功获取深交所 ETF {fund_code} {date_str} 申赎清单数据")
                break
        except Exception:
            continue
            
    if not content:
        print(f"获取深交所 ETF {fund_code} 申赎清单数据失败")
        return None
        
    metadata, components = parse_szse_etf_txt(content)
    _szse_pcf_cache[fund_code] = (metadata, components)
    return metadata, components


def get_market_by_stock_code(code: str) -> str:
    """根据证券代码前缀自动识别其所属市场 ID"""
    if code.startswith(('60', '68', '90')):
        return "101"  # 上海市场 (.SH)
    elif code.startswith(('00', '30', '20')):
        return "102"  # 深圳市场 (.SZ)
    elif code.startswith(('43', '83', '87', '88')):
        return "106"  # 北京市场 (.BJ)
    return "101"  # 默认上海


def get_szse_pcf_basic_info(fund_code: str) -> dict | None:
    """获取深交所 ETF 申赎清单基本信息，并映射为与上交所一致的字段"""
    res = _load_szse_pcf(fund_code)
    if not res:
        return None
    metadata, _ = res
    mapped_info = {
        "ESTIMATED_CASH_COMPONENT": metadata.get("预估现金差额", "0.0"),
        "NAV": metadata.get("基金份额净值", "0.0")
    }
    mapped_info.update(metadata)
    return mapped_info


def get_szse_pcf_components(fund_code: str) -> pd.DataFrame | None:
    """获取深交所 ETF 申赎清单成分股列表，并映射为与上交所一致的格式"""
    res = _load_szse_pcf(fund_code)
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
            
        market = get_market_by_stock_code(code)
        
        mapped_item = {
            "INSTRUMENT_ID": code,
            "UNDERLYION_SECURITY_ID": market,
            "QUANTITY": qty,
            "SUBSTITUTION_FLAG": sub_flag
        }
        mapped_list.append(mapped_item)
        
    return pd.DataFrame(mapped_list)


# ============================================================
# 统一对外接口（自动识别上交所/深交所）
# ============================================================
def get_pcf_basic_info(fund_code: str = "530100") -> dict | None:
    """获取 ETF 申赎清单基本信息（现金差额、最小申赎单位、净值等，支持沪深两市）"""
    if fund_code.startswith("1"):
        return get_szse_pcf_basic_info(fund_code)
    else:
        return get_sse_pcf_basic_info(fund_code)


def get_pcf_components(fund_code: str = "530100") -> pd.DataFrame | None:
    """获取 ETF 申赎清单成分股列表（支持沪深两市）"""
    if fund_code.startswith("1"):
        return get_szse_pcf_components(fund_code)
    else:
        return get_sse_pcf_components(fund_code)

# ============================================================
# 配置文件生成
# ============================================================
def generate_config(fund_code: str = "530100"):
    """
    从 PCF 申赎清单生成 alphacore_config.json 配置

    流程:
    1. 获取 PCF 成分股，筛选物理股票（排除"必须"现金替代）
    2. 获取各股票昨收价，计算 basket_pre_close
    3. 获取上证580指数昨收 index_pre_close
    4. 写入配置文件
    """
    print("=" * 70)
    print(f"  生成 {fund_code} alphacore 配置")
    print("=" * 70)

    # ---- 1. 获取 PCF 成分股 ----
    print("\n[1/4] 获取 PCF 申赎清单...")
    info = get_pcf_basic_info(fund_code)
    print(info)
    comp_df = get_pcf_components(fund_code)

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

    try:
        # 使用 count=30 避免指数因节假日或特殊停牌没有当天数据
        idx_data = xtdata.get_market_data(field_list=['close'], stock_list=[index_code], period='1d', count=30)
        if idx_data and 'close' in idx_data:
            idx_close_df = idx_data['close']
            if not idx_close_df.empty and index_code in idx_close_df.index:
                valid_idx = idx_close_df.loc[index_code].dropna()
                if not valid_idx.empty:
                    index_pre_close = float(valid_idx.iloc[-1])
    except Exception as e:
        print(f"  ❌ 获取指数昨收失败: {e}")
    
    if index_pre_close > 0:
        print(f"  指数 {index_code} 昨收: {index_pre_close}")
    else:
        print("  ⚠ 未能获取指数昨收，使用 0.0 占位")

    # ---- 4. 写入配置文件 ----
    print("\n[4/4] 写入配置文件...")
    project_root = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(project_root, "files", "alphacore_config.json")

    # 提取 estimated_cash 和 net_asset_value
    def clean_float(val):
        if not val: return 0.0
        val = str(val).replace('￥', '').replace(',', '').strip()
        try:
            return float(val)
        except ValueError:
            return 0.0
            
    estimated_cash = clean_float(info.get("ESTIMATED_CASH_COMPONENT", 0.0)) if info else 0.0
    net_asset_value = clean_float(info.get("NAV", 0.0)) if info else 0.0

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
        "components": components,
    }

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, ensure_ascii=False, indent=2)

    print(f"  配置已保存到: {config_path}")
    print(f"\n  --- 配置摘要 ---")
    print(f"  index_pre_close:  {index_pre_close}")
    print(f"  basket_pre_close: {basket_pre_close}")
    print(f"  estimated_cash:   {estimated_cash}")
    print(f"  net_asset_value:  {net_asset_value}")
    print(f"  components:       {len(components)} 只物理股票")

    print("\n" + "=" * 70)
    print("  完成")
    print("=" * 70)


if __name__ == "__main__":
    # 测试上交所 ETF 逻辑
    generate_config("530100")
    # 测试深交所 ETF 逻辑
    generate_config("159810")
