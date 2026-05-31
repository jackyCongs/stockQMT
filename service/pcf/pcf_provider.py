# service/pcf/pcf_provider.py
import os
import datetime
from abc import ABC, abstractmethod
import pandas as pd


class PcfProvider(ABC):
    """
    PCF 数据提供者抽象基类。
    定义统一的接口：get_basic_info / get_components，
    由上交所 (SsePcfProvider) 和深交所 (SzsePcfProvider) 子类实现。
    """

    def __init__(self, pcf_fetch_failures: list | None = None):
        """
        Args:
            pcf_fetch_failures: 由外部 Service 传入的共享列表，
                                子类在拉取失败时往里追加 (fund_code, reason) 元组。
        """
        self.pcf_fetch_failures = pcf_fetch_failures if pcf_fetch_failures is not None else []
        # 项目根目录：service/pcf/ → service/ → project_root
        self.project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # ─── 公共工具方法 ───────────────────────────────────────────────

    @staticmethod
    def clean_float(val) -> float:
        """清洗并解析浮点数字符串，兼容清除 ￥、元、逗号千分符等货币单位"""
        if not val:
            return 0.0
        val = str(val).replace('￥', '').replace('元', '').replace(',', '').strip()
        try:
            return float(val)
        except ValueError:
            return 0.0

    @staticmethod
    def get_market_by_stock_code(code: str) -> str:
        """根据证券代码前缀自动识别其所属市场 ID"""
        if code.startswith(('60', '68', '90')):
            return "101"  # 上海市场 (.SH)
        elif code.startswith(('00', '30', '20')):
            return "102"  # 深圳市场 (.SZ)
        elif code.startswith(('43', '83', '87', '88')):
            return "106"  # 北京市场 (.BJ)
        return "101"  # 默认上海

    def _get_pcf_dir(self) -> str:
        """获取当天的 PCF 缓存目录，不存在则自动创建"""
        today_str = datetime.datetime.now().strftime("%Y%m%d")
        pcf_dir = os.path.join(self.project_root, "files", "pcf", today_str)
        os.makedirs(pcf_dir, exist_ok=True)
        return pcf_dir

    # ─── 抽象接口 ──────────────────────────────────────────────────

    @abstractmethod
    def get_basic_info(self, fund_code: str) -> dict | None:
        """获取 ETF 申赎清单基本信息（现金差额、净值等）"""
        ...

    @abstractmethod
    def get_components(self, fund_code: str) -> pd.DataFrame | None:
        """获取 ETF 申赎清单成分股列表"""
        ...
