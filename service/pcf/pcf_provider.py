# service/pcf/pcf_provider.py
import os
import datetime
from abc import ABC, abstractmethod
import pandas as pd


class PcfProvider(ABC):
    """
    Abstract base class for PCF (Portfolio Composition File) data providers.
    Defines unified interfaces: get_basic_info / get_components.
    Implemented by SsePcfProvider (SSE) and SzsePcfProvider (SZSE) subclasses.
    """

    def __init__(self, pcf_fetch_failures: list | None = None):
        """
        Args:
            pcf_fetch_failures: Shared list passed from external services.
                                Subclasses append (fund_code, reason) tuples upon fetch failures.
        """
        self.pcf_fetch_failures = pcf_fetch_failures if pcf_fetch_failures is not None else []
        # Project root directory path calculation
        self.project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # ─── Public Utility Methods ───────────────────────────────────────────

    @staticmethod
    def clean_float(val) -> float:
        """Clean and parse float strings, handling currency symbols like ￥, Yuan, commas, etc."""
        if not val:
            return 0.0
        val = str(val).replace('￥', '').replace('元', '').replace(',', '').strip()
        try:
            return float(val)
        except ValueError:
            return 0.0

    @staticmethod
    def get_market_by_stock_code(code: str) -> str:
        """Identify market ID based on stock code prefix"""
        if code.startswith(('60', '68', '90')):
            return "101"  # 上海市场 (.SH)
        elif code.startswith(('00', '30', '20')):
            return "102"  # 深圳市场 (.SZ)
        elif code.startswith(('43', '83', '87', '88')):
            return "106"  # 北京市场 (.BJ)
        return "101"  # 默认上海

    def _get_pcf_dir(self) -> str:
        """Get the PCF cache directory for today, creating it if it doesn't exist"""
        today_str = datetime.datetime.now().strftime("%Y%m%d")
        pcf_dir = os.path.join(self.project_root, "files", "pcf", today_str)
        os.makedirs(pcf_dir, exist_ok=True)
        return pcf_dir

    # ─── Abstract Interfaces ──────────────────────────────────────────────

    @abstractmethod
    def get_basic_info(self, fund_code: str) -> dict | None:
        """Get basic ETF Portfolio Composition File (PCF) metadata (cash component, unit NAV, etc.)"""
        ...

    @abstractmethod
    def get_components(self, fund_code: str) -> pd.DataFrame | None:
        """Get ETF PCF components dataframe"""
        ...
