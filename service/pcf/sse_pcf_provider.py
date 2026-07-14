# service/pcf/sse_pcf_provider.py
import json
import os
import requests
import pandas as pd

from .pcf_provider import PcfProvider


class SsePcfProvider(PcfProvider):
    """SSE (Shanghai Stock Exchange) ETF PCF data provider"""

    def __init__(self, pcf_fetch_failures: list | None = None):
        super().__init__(pcf_fetch_failures)

    # ─── Interface Implementation ─────────────────────────────────────────

    def get_basic_info(self, fund_code: str) -> dict | None:
        """Get basic SSE ETF Portfolio Composition File (PCF) metadata"""
        pcf_dir = self._get_pcf_dir()
        cache_file = os.path.join(pcf_dir, f"SSE_{fund_code}_basic.json")

        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    print(f"  📖 [Cache Hit] Successfully loaded local SSE ETF {fund_code} basic metadata")
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
            print(f"  🌐 [Network Request] Local cache miss. Fetching SSE ETF {fund_code} basic metadata from server...")
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data and data.get("result") and len(data["result"]) > 0:
                result_data = data["result"][0]
                print(f"  🌐 [Network Success] Successfully retrieved basic metadata for SSE ETF {fund_code}")
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(result_data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                return result_data
            self.pcf_fetch_failures.append((fund_code, "SSE PCF basic info API returned empty dataset"))
            return None
        except Exception as e:
            print(f"Failed to fetch SSE PCF basic info: {e}")
            self.pcf_fetch_failures.append((fund_code, f"SSE PCF basic info request exception: {e}"))
            return None

    def get_components(self, fund_code: str) -> pd.DataFrame | None:
        """Get SSE ETF PCF components list"""
        pcf_dir = self._get_pcf_dir()
        cache_file = os.path.join(pcf_dir, f"SSE_{fund_code}_comp.json")

        result_list = None
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    result_list = json.load(f)
                    print(f"  📖 [Cache Hit] Successfully loaded local SSE ETF {fund_code} components")
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
                print(f"  🌐 [Network Request] Local cache miss. Fetching SSE ETF {fund_code} components from server...")
                resp = requests.get(url, headers=headers, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                if data and data.get("result") and len(data["result"]) > 0:
                    result_list = data["result"]
                    print(f"  🌐 [Network Success] Successfully retrieved components for SSE ETF {fund_code}")
                    try:
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump(result_list, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
                else:
                    self.pcf_fetch_failures.append((fund_code, "SSE PCF components API returned empty dataset"))
                    return None
            except Exception as e:
                print(f"Failed to fetch SSE PCF components: {e}")
                self.pcf_fetch_failures.append((fund_code, f"SSE PCF components request exception: {e}"))
                return None

        try:
            df = pd.DataFrame(result_list)

            # Clean SSE-specific cash substitution amount (SUBSTITUTION_CASH_AMOUNT) and map it to column '申购替代金额'
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
            print(f"Failed to parse SSE PCF components: {e}")
            return None
