# service/pcf/sse_pcf_provider.py
import json
import os
import requests
import pandas as pd

from .pcf_provider import PcfProvider


class SsePcfProvider(PcfProvider):
    """上交所 ETF PCF 数据提供者"""

    def __init__(self, pcf_fetch_failures: list | None = None):
        super().__init__(pcf_fetch_failures)

    # ─── 接口实现 ──────────────────────────────────────────────────

    def get_basic_info(self, fund_code: str) -> dict | None:
        """获取上交所 ETF 申赎清单基本信息"""
        pcf_dir = self._get_pcf_dir()
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
            print(f"  🌐 [网络请求] 本地无基本信息缓存，正在向上交所服务器拉取 ETF {fund_code} ...")
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

    def get_components(self, fund_code: str) -> pd.DataFrame | None:
        """获取上交所 ETF 申赎清单成分股列表"""
        pcf_dir = self._get_pcf_dir()
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
                print(f"  🌐 [网络请求] 本地无成分股缓存，正在向上交所服务器拉取 ETF {fund_code} ...")
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
