# service/pcf/szse_pcf_provider.py
import os
import glob
import datetime
import random
import requests
import pandas as pd

from .pcf_provider import PcfProvider


class SzsePcfProvider(PcfProvider):
    """深交所 ETF PCF 数据提供者"""

    def __init__(self, pcf_fetch_failures: list | None = None):
        super().__init__(pcf_fetch_failures)
        self._pcf_cache = {}

    # ─── 内部方法 ──────────────────────────────────────────────────

    def _parse_etf_txt(self, content: str) -> tuple[dict, list]:
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

    def _load_pcf(self, fund_code: str) -> tuple[dict, list] | None:
        """动态获取并解析深交所 ETF PCF 清单数据（自动向前回溯 5 天以兼容节假日/非交易时间）"""
        if fund_code in self._pcf_cache:
            return self._pcf_cache[fund_code]

        now = datetime.datetime.now()
        pcf_dir = self._get_pcf_dir()

        content = None
        success_date_str = None

        # 1. 尝试从本地缓存读取
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

        metadata, components = self._parse_etf_txt(content)
        if success_date_str:
            metadata['TRADING_DAY'] = success_date_str
        self._pcf_cache[fund_code] = (metadata, components)
        return metadata, components

    # ─── 接口实现 ──────────────────────────────────────────────────

    def get_basic_info(self, fund_code: str) -> dict | None:
        """获取深交所 ETF 申赎清单基本信息，并映射为与上交所一致的字段"""
        res = self._load_pcf(fund_code)
        if not res:
            return None
        metadata, _ = res
        mapped_info = {
            "ESTIMATED_CASH_COMPONENT": metadata.get("预估现金差额", "0.0"),
            "NAV": metadata.get("基金份额净值", "0.0")
        }
        mapped_info.update(metadata)
        return mapped_info

    def get_components(self, fund_code: str) -> pd.DataFrame | None:
        """获取深交所 ETF 申赎清单成分股列表，并映射为与上交所一致的格式"""
        res = self._load_pcf(fund_code)
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
