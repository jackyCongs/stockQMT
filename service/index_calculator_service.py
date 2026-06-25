import os
import json
import time
import logging
import warnings
from datetime import datetime, timedelta
import pandas as pd
from xtquant import xtdata
from db.idx_components import save_index_components, get_active_components
from db.index_daily_history import get_index_pre_close

logger = logging.getLogger(__name__)


class IndexReplicationCalculator:
    """
    AlphaCore 盘前总引擎 (终极融合防弹版)
    负责：文件解析计算 -> 数据库增量对齐 -> 组装下发 JSON -> 唤醒 QMT 行情订阅
    """

    def __init__(self, db_pool, base_capital=1000000000):
        self.db_pool = db_pool
        self.base_capital = base_capital

        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.project_root = os.path.dirname(current_dir)
        self.download_dir = os.path.join(self.project_root, 'files', 'index_files')
        self.export_path = os.path.join(self.project_root, 'files', 'alphacore_config.json')

    @staticmethod
    def format_component_code(raw_code):
        """【共享工具】成分股(个股)专用 QMT 后缀补全逻辑"""
        raw_str = str(raw_code).strip().split('.')[0]
        
        # 港股通股票通常为 5 位数，无需补齐 6 位
        if len(raw_str) == 5 and raw_str.isdigit():
            return f"{raw_str}.HK"
            
        code_str = raw_str.zfill(6)
        if code_str.startswith('60') or code_str.startswith('68') or code_str.startswith('51') or code_str.startswith('56') or code_str.startswith('58'):
            return f"{code_str}.SH"
        elif code_str.startswith('00') or code_str.startswith('30') or code_str.startswith('15'):
            return f"{code_str}.SZ"
        elif code_str.startswith('8') or code_str.startswith('4') or code_str.startswith('92') or code_str.startswith('93'):
            return f"{code_str}.BJ"
        return code_str

    def _ensure_history_data(self, stock_list, period, start_time, end_time, max_retries=15):
        """
        【神级对齐】带智能校验的强同步下载器，杜绝任何一只股票市值丢失！
        """
        missing_stocks = set(stock_list)

        logger.info(f"⏬ 开始校验并拉取 {len(missing_stocks)} 只标的的 {period} 行情...")

        for attempt in range(max_retries):
            if not missing_stocks:
                break

            # 1. 尝试从本地提取 (坚决用 'front' 前复权维持市值守恒！)
            market_data = xtdata.get_market_data_ex(
                field_list=['close'],
                stock_list=list(missing_stocks),
                period=period,
                start_time=start_time,
                end_time=end_time,
                dividend_type='front'
            )

            # 2. 检查哪些没拿到
            still_missing = set()
            for code in missing_stocks:
                # 判断条件：没有这个键，或者为空 (说明数据还没下完落盘)
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
                logger.info("✅ 所有标的历史数据已 100% 落盘，完美就绪！")
                # 🟢 【Bug Fix】跳出前清空集合，防止外层误报 ERROR
                missing_stocks = set()
                break

            missing_stocks = still_missing
            logger.warning(f"⏳ 第 {attempt + 1} 次校验: 尚有 {len(missing_stocks)} 只标的未就绪，正在补漏拉取...")

            # 3. 仅对缺失的股票发射异步下载指令
            def dummy_cb(data):
                pass

            for i, qmt_code in enumerate(missing_stocks):
                try:
                    xtdata.download_history_data(qmt_code, period, start_time, end_time, dummy_cb)
                except Exception:
                    pass
                # 稍微限流，防止 QMT 底层 C++ 拥堵
                if (i + 1) % 200 == 0:
                    time.sleep(0.1)

            # 4. 强制等待 3 秒，给 C++ 写入本地缓存的时间，然后进入下一轮校验循环
            time.sleep(3.0)

        if missing_stocks:
            logger.error(
                f"⚠️ 警告！历经 {max_retries} 次重试，仍有 {len(missing_stocks)} 只股票(如 {list(missing_stocks)[:5]}) 无法获取数据，请检查是否退市！")

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

    # =====================================================================
    #  阶段一：洗盘计算层 (读取文件 -> 查停牌 -> 算虚拟股数 -> 存入数据库)
    # =====================================================================
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

            if not qmt_stock_list:
                continue

            base_dt = datetime.strptime(base_date_str, '%Y%m%d')
            start_dt = base_dt - timedelta(days=90)
            start_search_date_str = start_dt.strftime('%Y%m%d')

            db_date = f"{base_date_str[:4]}-{base_date_str[4:6]}-{base_date_str[6:]}"

            # 【替换点 1】：洗盘计算时，如果遇到超大宽基(如中证1000/2000)，也使用安全分批下载
            self._ensure_history_data(
                stock_list=qmt_stock_list,
                period='1d',
                start_time=start_search_date_str,
                end_time=base_date_str
            )

            # 注意：get_market_data_ex 是从本地硬盘/内存读取，不走网络，不存在并发死锁问题，无需分批
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

    # =====================================================================
    #  阶段二：点火下发层 (拉取 DB 数据 -> 追溯昨收价算除数 -> 组装 JSON -> QMT 批量订阅)
    # =====================================================================
    def ignite_engine(self, yesterday_str):
        """
        点火核心流程：从数据库拉取计算好的数据 -> 下发 JSON -> 唤醒 QMT 全量订阅
        :param yesterday_str: 昨天交易日的字符串，格式 'YYYY-MM-DD' (如 '2026-05-18')
        """
        logger.info(f"\n=== [AlphaCore] 开始构建 Golang 引擎启动载荷 (T-1基准: {yesterday_str}) ===")

        components_map = get_active_components(self.db_pool)
        if not components_map:
            logger.error("未找到任何有效成分股，点火中止！请先执行 process_all_indices()")
            return

        pre_close_map = get_index_pre_close(self.db_pool, yesterday_str)
        if not pre_close_map:
            logger.error(f"未能获取到 {yesterday_str} 的指数昨收数据，点火中止！")
            return

        golang_payload = {}
        all_unique_qmt_stocks = set()

        for stock_list in components_map.values():
            for item in stock_list:
                all_unique_qmt_stocks.add(self.format_component_code(item['stock_code']))

        subscribe_list = list(all_unique_qmt_stocks)

        qmt_yesterday = yesterday_str.replace('-', '')
        yest_dt = datetime.strptime(qmt_yesterday, '%Y%m%d')
        start_yest_dt = yest_dt - timedelta(days=90)
        start_yest_str = start_yest_dt.strftime('%Y%m%d')

        # 【替换点 2】：全市场去重后几千只股票的终极大关卡！启用分批下载，彻底告别死锁！
        self._ensure_history_data(
            stock_list=subscribe_list,
            period='1d',
            start_time=start_yest_str,
            end_time=qmt_yesterday
        )
        logger.info("强制等待 5 秒让底层数据落盘...")
        time.sleep(5.0)

        yesterday_market_data = xtdata.get_market_data_ex(
            field_list=['close'],
            stock_list=subscribe_list,
            period='1d',
            start_time=start_yest_str,
            end_time=qmt_yesterday,
            dividend_type='front'
        )

        for index_code, stock_list in components_map.items():
            if index_code not in pre_close_map:
                logger.warning(f"[{index_code}] 缺失昨收点位，该指数将被移出今日计算序列。")
                continue

            pre_close_point = pre_close_map[index_code]
            formatted_components = {}
            yesterday_synthetic_market_cap = 0.0

            for item in stock_list:
                qmt_code = self.format_component_code(item['stock_code'])
                qi = item['synthetic_shares']
                formatted_components[qmt_code] = qi

                yesterday_price = 0.0
                if qmt_code in yesterday_market_data and not yesterday_market_data[qmt_code].empty:
                    df_px = yesterday_market_data[qmt_code]

                    if qmt_yesterday in df_px.index:
                        px_val = df_px.loc[qmt_yesterday, 'close']
                        if pd.notna(px_val) and float(px_val) > 0:
                            yesterday_price = float(px_val)

                    if yesterday_price <= 0:
                        valid_history_df = df_px[pd.notna(df_px['close'])]
                        if not valid_history_df.empty:
                            yesterday_price = float(valid_history_df['close'].iloc[-1])

                if yesterday_price > 0:
                    yesterday_synthetic_market_cap += yesterday_price * qi
                else:
                    logger.warning(f"[{index_code}] 极度异常: 成分股 {qmt_code} 连续90天无交易数据，除数可能失真！")

            if pre_close_point > 0 and yesterday_synthetic_market_cap > 0:
                divisor = yesterday_synthetic_market_cap / pre_close_point
            else:
                divisor = 1.0

            golang_payload[index_code] = {
                "pre_close": pre_close_point,
                "divisor": divisor,
                "components": formatted_components
            }

        try:
            with open(self.export_path, 'w', encoding='utf-8') as f:
                json.dump(golang_payload, f, ensure_ascii=False, indent=2)
            logger.info(f"✅ 成功生成 AlphaCore 配置文件: {self.export_path}")
            logger.info(f"   -> 包含成功挂载健康指数: {len(golang_payload)} 个")
        except Exception as e:
            logger.error(f"JSON 配置文件下发失败: {e}")
            return

    # =====================================================================
    #  提供一个终极傻瓜式一键运行方法
    # =====================================================================
    def run_daily_pipeline(self, yesterday_str):
        """一键打包运行盘前的两大生命周期"""
        self.process_all_indices()
        self.ignite_engine(yesterday_str)