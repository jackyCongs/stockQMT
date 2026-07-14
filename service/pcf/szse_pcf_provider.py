# service/pcf/szse_pcf_provider.py
import os
import glob
import datetime
import random
import requests
import pandas as pd

from .pcf_provider import PcfProvider


class SzsePcfProvider(PcfProvider):
    """SZSE (Shenzhen Stock Exchange) ETF PCF data provider"""

    def __init__(self, pcf_fetch_failures: list | None = None):
        super().__init__(pcf_fetch_failures)
        self._pcf_cache = {}

    # ─── Internal Methods ─────────────────────────────────────────────────

    def _parse_etf_txt(self, content: str) -> tuple[dict, list]:
        """
        Parse SZSE ETF PCF text file content.
        Supports parsing base metadata and index components.
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
        """Retrieve and parse SZSE ETF PCF data dynamically (looks back 5 days to handle weekends/holidays)"""
        if fund_code in self._pcf_cache:
            return self._pcf_cache[fund_code]

        now = datetime.datetime.now()
        pcf_dir = self._get_pcf_dir()

        content = None
        success_date_str = None

        # 1. Try loading from local cache
        cache_files = glob.glob(os.path.join(pcf_dir, f"SZSE_{fund_code}_*.txt"))
        if cache_files:
            cache_file = cache_files[0]
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    content = f.read()
                filename = os.path.basename(cache_file)
                success_date_str = filename.replace(f"SZSE_{fund_code}_", "").replace(".txt", "")
                print(f"  📖 [Cache Hit] Successfully loaded local SZSE ETF {fund_code} ({success_date_str}) PCF data")
            except Exception as e:
                print(f"  ❌ Failed to read local cache: {e}")
                content = None
                success_date_str = None

        # 2. Fetch from network if not cached, then save to local cache
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
                        print(f"  🌐 [Network Success] Successfully retrieved SZSE ETF {fund_code} ({date_str}) PCF data")

                        # Save to local file cache
                        cache_file = os.path.join(pcf_dir, f"SZSE_{fund_code}_{success_date_str}.txt")
                        try:
                            with open(cache_file, "w", encoding="utf-8") as f:
                                f.write(content)
                        except Exception as e:
                            print(f"  ❌ Failed to write to local cache: {e}")
                        break
                except Exception:
                    continue

        if not content:
            print(f"Failed to retrieve SZSE ETF {fund_code} PCF data")
            self.pcf_fetch_failures.append((fund_code, "SZSE PCF data fetch failed (no data returned or network anomaly over 5-day lookback)"))
            return None

        metadata, components = self._parse_etf_txt(content)
        if success_date_str:
            metadata['TRADING_DAY'] = success_date_str
        self._pcf_cache[fund_code] = (metadata, components)
        return metadata, components

    # ─── Interface Implementation ─────────────────────────────────────────

    def get_basic_info(self, fund_code: str) -> dict | None:
        """Get basic SZSE ETF PCF metadata, mapping keys to align with SSE field format"""
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
        """Get SZSE ETF PCF components list, mapping columns to align with SSE dataframe layout"""
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

            # Filter out irrelevant substitution details
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
