import os
import re
import time
import logging
import warnings
import pandas as pd
from db.idx_components import save_index_components
from xtquant import xtdata

logger = logging.getLogger(__name__)


class IndexReplicationCalculator:
    def __init__(self, db_pool, base_capital=1000000000):
        self.db_pool = db_pool
        self.base_capital = base_capital

        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.project_root = os.path.dirname(current_dir)
        self.download_dir = os.path.join(self.project_root, 'files', 'index_files')

    @staticmethod
    def format_component_code(raw_code):
        """成分股(个股)专用 QMT 后缀补全逻辑"""
        code_str = str(raw_code).strip().split('.')[0].zfill(6)
        if code_str.startswith('60') or code_str.startswith('68'):
            return f"{code_str}.SH"
        elif code_str.startswith('00') or code_str.startswith('30'):
            return f"{code_str}.SZ"
        elif code_str.startswith('8') or code_str.startswith('4'):
            return f"{code_str}.BJ"
        return code_str

    def _load_dataframe(self, file_path):
        """统一且健壮的装甲加载器"""
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                return pd.read_excel(file_path)
        except ImportError as e:
            logger.error(f"[环境错误] 缺少库: {e}，请运行 pip install xlrd openpyxl")
            return pd.DataFrame()
        except Exception:
            for enc in ['gbk', 'utf-8', 'gb18030', 'latin1']:
                try:
                    df = pd.read_csv(file_path, sep=None, engine='python', encoding=enc)
                    if not df.empty:
                        return df
                except:
                    continue
            return pd.DataFrame()

    def _find_column(self, df, keywords):
        """智能寻找对应的列名"""
        for col in df.columns:
            col_str = str(col).lower()
            if any(k in col_str for k in keywords):
                return col
        return None

    def process_all_indices(self):
        """遍历所有下载的文件，计算并增量留痕入库"""
        if not os.path.exists(self.download_dir):
            logger.error(f"下载目录不存在: {self.download_dir}")
            return

        files = [f for f in os.listdir(self.download_dir) if f.endswith(('.xls', '.xlsx'))]
        logger.info(f"开始处理本地权重文件，共计 {len(files)} 个...")

        for file_name in files:
            parts = file_name.split('_')
            if len(parts) < 2:
                continue

            base_date_str = parts[0]
            index_code = parts[1]
            file_path = os.path.join(self.download_dir, file_name)

            df = self._load_dataframe(file_path)
            if df.empty:
                continue

            col_code = self._find_column(df, ['代码', 'code'])
            col_name = self._find_column(df, ['简称', '名称', 'name'])
            col_weight = self._find_column(df, ['权重', 'weight'])

            if not col_code or not col_weight:
                logger.warning(f"[{index_code}] 文件缺少核心字段(代码/权重)，跳过。")
                continue

            qmt_stock_list = []
            component_meta = {}

            for index, row in df.iterrows():
                raw_code = str(row[col_code]).strip()
                if raw_code == 'nan' or not raw_code:
                    continue
                raw_name = str(row[col_name]).strip() if col_name else ""
                try:
                    raw_weight = float(row[col_weight])
                except ValueError:
                    continue

                qmt_code = self.format_component_code(raw_code)
                qmt_stock_list.append(qmt_code)
                component_meta[qmt_code] = {
                    'stock_code': raw_code.split('.')[0].zfill(6),
                    'stock_name': raw_name,
                    'weight_percent': raw_weight
                }

            db_date = f"{base_date_str[:4]}-{base_date_str[4:6]}-{base_date_str[6:]}"

            xtdata.download_history_data2(qmt_stock_list, period='1d', start_time=base_date_str, end_time=base_date_str)
            time.sleep(0.2)

            market_data = xtdata.get_market_data_ex(
                field_list=['close'],
                stock_list=qmt_stock_list,
                period='1d',
                start_time=base_date_str,
                end_time=base_date_str,
                dividend_type='front'
            )

            db_records = []

            for qmt_code in qmt_stock_list:
                meta = component_meta[qmt_code]

                # 动态审计
                detail = xtdata.get_instrument_detail(qmt_code)
                if detail is None:
                    status = 0
                    initial_remark = "盘前审计：QMT基础资料缺失/疑似退市"
                else:
                    status = 1  # 正常交易
                    initial_remark = ""

                base_price = 0.0
                if qmt_code in market_data and not market_data[qmt_code].empty:
                    df_px = market_data[qmt_code]
                    if len(df_px) > 0:
                        base_price = float(df_px['close'].iloc[0])

                synthetic_shares = 0.0
                if base_price > 0:
                    actual_weight = meta['weight_percent'] / 100.0
                    synthetic_shares = (self.base_capital * actual_weight) / base_price
                else:
                    # 如果拿不到开盘基准价，说明今天可能突发停牌爆雷，记录到备注
                    initial_remark = "数据异常：未能获取到前复权基准价"

                # 构建符合最新带 remark 字段的数据字典
                record = {
                    'index_code': index_code,
                    'index_name': '',
                    'stock_code': meta['stock_code'],
                    'stock_name': meta['stock_name'],
                    'base_date': db_date,
                    'weight_percent': meta['weight_percent'],
                    'base_price': base_price,
                    'synthetic_shares': synthetic_shares,
                    'status': status,
                    'remark': initial_remark  # 塞入初始备注，适配新列
                }
                db_records.append(record)

            # 执行入库编排
            save_index_components(self.db_pool, index_code, db_records)