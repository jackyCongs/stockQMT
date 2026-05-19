import os
import re
import time
import logging
import warnings
from datetime import datetime, timedelta
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
        # 【核心修复】兼容北交所新规：除了老旧的 8/4 字头，全线吃进 2024 年上线的 920 新代码序列
        elif code_str.startswith('8') or code_str.startswith('4') or code_str.startswith('920'):
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

    def _find_column(self, df, include_keywords, exclude_keywords=None):
        """智能寻找对应的列名，支持排除特定关键字以免误判"""
        if exclude_keywords is None:
            exclude_keywords = []

        for col in df.columns:
            col_str = str(col).lower()
            if any(k in col_str for k in include_keywords):
                if any(ex_k in col_str for ex_k in exclude_keywords):
                    continue
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

            col_code = self._find_column(df, ['代码', 'code'], exclude_keywords=['指数', 'index'])
            col_name = self._find_column(df, ['简称', '名称', 'name'], exclude_keywords=['指数', 'index'])
            col_weight = self._find_column(df, ['权重', 'weight'])

            if not col_code or not col_weight:
                logger.warning(f"[{index_code}] 文件缺少核心字段(成分券代码/权重)，跳过。")
                continue

            qmt_stock_list = []
            component_meta = {}

            for index, row in df.iterrows():
                raw_code = str(row[col_code]).strip()
                if raw_code == 'nan' or not raw_code:
                    continue

                raw_name = str(row[col_name]).strip() if col_name else ""
                if raw_name.lower() == 'nan':
                    raw_name = ""

                raw_weight_val = row[col_weight]
                if pd.isna(raw_weight_val):
                    continue

                try:
                    raw_weight = float(raw_weight_val)
                    if pd.isna(raw_weight):
                        continue
                except ValueError:
                    continue

                qmt_code = self.format_component_code(raw_code)
                qmt_stock_list.append(qmt_code)
                component_meta[qmt_code] = {
                    'stock_code': raw_code.split('.')[0].zfill(6),
                    'stock_name': raw_name,
                    'weight_percent': raw_weight
                }

            base_dt = datetime.strptime(base_date_str, '%Y%m%d')
            start_dt = base_dt - timedelta(days=90)
            start_search_date_str = start_dt.strftime('%Y%m%d')

            db_date = f"{base_date_str[:4]}-{base_date_str[4:6]}-{base_date_str[6:]}"

            # =======================================================
            # 撤销全部防御：不拦截空列表，不使用 try-except
            # 让 QMT 的原生调用和潜在的任何异常直接赤裸裸暴露在控制台
            # =======================================================
            xtdata.download_history_data2(
                qmt_stock_list,
                period='1d',
                start_time=start_search_date_str,
                end_time=base_date_str
            )
            time.sleep(0.2)

            market_data = xtdata.get_market_data_ex(
                field_list=['close'],
                stock_list=qmt_stock_list,
                period='1d',
                start_time=start_search_date_str,
                end_time=base_date_str,
                dividend_type='front'
            )

            db_records = []

            for qmt_code in qmt_stock_list:
                meta = component_meta[qmt_code]

                detail = xtdata.get_instrument_detail(qmt_code)
                if detail is None:
                    status = 0
                    initial_remark = "盘前审计：QMT基础资料缺失/疑似退市"
                else:
                    status = 1
                    initial_remark = "官方在册：正常运作"

                base_price = 0.0
                is_suspended_on_base = False

                if qmt_code in market_data and not market_data[qmt_code].empty:
                    df_px = market_data[qmt_code]

                    if base_date_str in df_px.index:
                        px_val = df_px.loc[base_date_str, 'close']
                        if pd.notna(px_val) and float(px_val) > 0:
                            base_price = float(px_val)

                    if base_price <= 0:
                        valid_history_df = df_px[pd.notna(df_px['close'])]
                        if not valid_history_df.empty:
                            base_price = float(valid_history_df['close'].iloc[-1])
                            is_suspended_on_base = True

                synthetic_shares = 0.0
                if base_price > 0:
                    actual_weight = meta['weight_percent'] / 100.0
                    synthetic_shares = (self.base_capital * actual_weight) / base_price

                    if is_suspended_on_base:
                        initial_remark = "官方在册：基准日停牌(已自动追溯复牌前最后收盘价)"
                else:
                    initial_remark = "数据异常：向前追溯90天仍未能获取到任何有效价格"

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
                    'remark': initial_remark
                }
                db_records.append(record)

            save_index_components(self.db_pool, index_code, db_records)