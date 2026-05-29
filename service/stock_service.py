# coding=utf-8
import datetime
import time
import logging
from collections import defaultdict

from xtquant import xtdata
import helper.utils
from db import stock, index_daily_history
from helper import utils, spider, notifier
from helper.data_loader import calc_daily_excess_volatility_batch, get_penalty


def read_lines_to_array(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            # 读取所有行并去除每行末尾的换行符
            lines = [line.strip().strip('"') for line in file]
            return lines
    except FileNotFoundError:
        logger.error(f"错误：文件 '{file_path}' 不存在")
        return []
    except Exception as e:
        logger.error(f"错误：读取文件时发生异常: {e}")
        return []

logger = logging.getLogger(__name__)

class StockService:
    def __init__(self, db):
        self.format_codes = []
        self.stock_codes = []
        self.index_codes = []
        self.success_indices_set = []
        self.db = db
        self.trade_date = ""

    def load_all_stock_codes(self):
        last_id = 0  # 初始锚点
        batch_size = 500
        total_processed = 0
        while True:
            # 1. 获取当前批次
            stocks = stock.get_stock_batch(self.db, last_id, batch_size)
            # 异常处理：如果返回 None 说明数据库连接或查询报错
            if stocks is None:
                print("查询过程中出现错误，终止处理。")
                break
            # 2. 检查是否还有数据：如果结果集为空，说明已经查完了
            if not stocks:
                print("所有数据处理完毕。")
                break
            # 3. 处理当前批次的数据
            for detail in stocks:
                self.stock_codes.append(detail['code'])
            last_id = stocks[-1]['id']
            total_processed += len(stocks)
            print(f"已加载 {total_processed} 条数据，当前 ID 锚点: {last_id}")

        print(f"最终共加载 {total_processed} 条记录。")

    def format_code(self):
        for code in self.stock_codes:
            if utils.enhance_stock_code(code) == code:
                continue
            self.format_codes.append(utils.enhance_stock_code(code))

    def update_stock_price(self):
        self.load_all_stock_codes()
        self.format_code()
        batch_size = 50
        for i in range(0, len(self.format_codes), batch_size):
            batch_codes = self.format_codes[i: i + batch_size]
            seq = xtdata.subscribe_whole_quote(batch_codes, callback=self.stocks_handler)
            logger.info(f"批次 {i // batch_size + 1} 订阅成功，包含 {len(batch_codes)} 只股票，订阅ID: {seq}")
            time.sleep(2)
        time.sleep(1)
        logger.info("所有批次订阅请求发送完毕")

    def stocks_handler(self, msgs):
        update_data = []
        for code in msgs:
            # stock.update_stock_price(self.db, msgs[code]['lastPrice'], helper.utils.purified_code(code))
            update_data.append((helper.utils.purified_code(code), msgs[code]['lastPrice']))
        # 2. 调用批量更新函数
        if update_data:
            stock.batch_update_stock_price(self.db, update_data)
            logger.info(f"Batch update successful: {len(update_data)} stocks")
        logger.info(f"{len(update_data)} 条数据 updated successful")

    def update_index_daily_history(self, fund_spider_cookie):
        """
        发起批量订阅，获取指数全量快照数据
        """
        # 假设你已经有了一个类似 load_all_index_codes 的方法将代码存入了 self.index_codes
        # 如果没有，可以直接写死你要追踪的三大指数/宽基指数
        self.success_indices_set = set()
        self.index_codes = stock.get_unique_index_codes(self.db)
        benchmark_indices = ['000001', '399001', '000300']
        for code in benchmark_indices:
            if code not in self.index_codes:
                self.index_codes.append(code)
        # 2. 格式化代码 (复用你的 utils，确保符合 xtdata 要求)
        formatted_indices = [utils.enhance_stock_code(code, "index") for code in self.index_codes]

        # 3. 分批订阅 (指数数量通常不多，但为了代码健壮性依然保留分批逻辑)
        batch_size = 50
        for i in range(0, len(formatted_indices), batch_size):
            batch_codes = formatted_indices[i: i + batch_size]
            seq = xtdata.subscribe_whole_quote(batch_codes, callback=self.indices_handler)
            logger.info(f"指数批次 {i // batch_size + 1} 订阅成功，包含 {len(batch_codes)} 个指数，订阅ID: {seq}")
            time.sleep(1)  # 指数数据量小，稍微缩短 sleep 时间即可
            
        # --- NEW: Get all active ETFs and subscribe ---
        conn = self.db.get_connection()
        cursor = conn.cursor()
        etf_codes = []
        try:
            cursor.execute("SELECT code FROM stock WHERE is_etf = 1 AND status = 1 AND inner_etf_type = 'etf'")
            etf_rows = cursor.fetchall()
            etf_codes = [row[0] for row in etf_rows]
            formatted_etfs = [helper.utils.enhance_stock_code(code) for code in etf_codes]
            for i in range(0, len(formatted_etfs), batch_size):
                batch_codes = formatted_etfs[i: i + batch_size]
                seq = xtdata.subscribe_whole_quote(batch_codes, callback=self.etf_handler)
                logger.info(f"ETF批次 {i // batch_size + 1} 订阅成功，包含 {len(batch_codes)} 个ETF，订阅ID: {seq}")
                time.sleep(1)
        except Exception as e:
            logger.error(f"查询ETF失败: {e}")
        finally:
            cursor.close()
            conn.close()
        logger.info("开始检查剩余指数部分，将通过第三方数据更新......")
        time.sleep(20)
        # 1. 算差集：找出没收到的漏网之鱼
        rest_index_codes = list(set(formatted_indices) - self.success_indices_set)

        if rest_index_codes:
            logger.warning(f"启动第三方兜底，处理 {len(rest_index_codes)} 个缺失指数: {rest_index_codes}")
            third_party_update_data = []
            for code in rest_index_codes:
                try:
                    clean_code = code.split('.')[0]
                    # 2. 调用我们刚才写的同步强行阻断函数
                    logger.info(f"正在通过第三方数据流抓取 {clean_code} 的快照...")
                    json_data = helper.spider.fetch_single_snapshot_safe(clean_code, fund_spider_cookie)
                    if not json_data:
                        logger.warning(f"第三方接口未能获取到 {code} 的数据")
                        continue
                    # 3. 复用之前写的 JSON 清洗函数
                    parsed_rows = self.parse_third_party_index_json(clean_code, json_data, "EastMoney")
                    if parsed_rows:
                        third_party_update_data.extend(parsed_rows)
                        logger.info(f"成功挽救 {clean_code} 数据。")
                except Exception as e:
                    logger.error(f"通过第三方获取指数 {code} 失败: {e}")
                # 安全间隔，虽然我们马上切断了连接，但连续发起建连请求依然容易被风控
                time.sleep(1)
                # 批量入库
            if third_party_update_data:
                index_daily_history.batch_upsert_index_history(self.db, third_party_update_data, 'index')
                logger.info(f"第三方兜底任务完成，成功挽救 {len(third_party_update_data)} 条数据")
        else:
            logger.info("完美！所有指数都已通过官方通道获取成功，无需调用第三方兜底。")

        logger.info("所有批次订阅请求发送完毕")
        #开始校验指数
        updated_index_codes = index_daily_history.get_updated_index_codes_by_date(self.db, self.trade_date, 'index')
        if len(self.index_codes) == len(updated_index_codes):
            logger.info(f"✅ 指数数据校验通过：预期 {len(self.index_codes)} 条，实际 {len(updated_index_codes)} 条。")
        else:
            # 找出缺失的具体代码
            missing_codes = set(self.index_codes) - set(updated_index_codes)
            alert_msg = (
                f"交易日期: {self.trade_date}\n"
                f"预期指数数量: {len(self.index_codes)}\n"
                f"实际指数数量: {len(updated_index_codes)}\n"
                f"缺失差额: {len(missing_codes)}\n"
                f"缺失列表: {list(missing_codes)[:10]}..."  # 仅展示前10个防止刷屏
            )
            notifier.send_telegram_alert("🚨 【更新指数数据缺失报警】", alert_msg)
            logger.error(alert_msg)

        #开始校验ETF
        updated_etf_codes = index_daily_history.get_updated_index_codes_by_date(self.db, self.trade_date, 'etf')
        if len(etf_codes) == len(updated_etf_codes):
            logger.info(f"✅ ETF数据校验通过：预期 {len(etf_codes)} 条，实际 {len(updated_etf_codes)} 条。")
        else:
            # 找出缺失的具体代码
            missing_etfs = set(etf_codes) - set(updated_etf_codes)
            alert_msg = (
                f"交易日期: {self.trade_date}\n"
                f"预期ETF数量: {len(etf_codes)}\n"
                f"实际ETF数量: {len(updated_etf_codes)}\n"
                f"缺失差额: {len(missing_etfs)}\n"
                f"缺失列表: {list(missing_etfs)[:10]}..."  # 仅展示前10个防止刷屏
            )
            notifier.send_telegram_alert("🚨 【更新ETF数据缺失报警】", alert_msg)
            logger.error(alert_msg)
        # 计算惩罚值
        self.calculate_and_save_daily_penalty(self.trade_date)

    def calculate_and_save_daily_penalty(self, trade_date):
        """
        盘后执行：组装过去3天的数据字典，喂给核心引擎计算惩罚值并落盘
        """
        conn = self.db.get_connection()
        try:
            rows = index_daily_history.get_3_days_history_list(self.db, trade_date)
            # 3. 在内存中将数据按指数代码分组整理
            # 格式: {'000001.SH': [0.001, -0.002, 0.005], ...}
            history_map = defaultdict(list)
            for row in rows:
                code, t_date, amplitude = row
                history_map[code].append(float(amplitude))
            # 4. 提取基准指数的 3 天波动率列表 (构建 index_rates_list)
            # 假设你的三大宽基指数是这三个，如果没有数据则默认全0
            benchmarks = ['000001', '399001', '000300']
            index_rates_list = []
            for bm_code in benchmarks:
                if bm_code in history_map and len(history_map[bm_code]) == 3:
                    index_rates_list.append(history_map[bm_code])
                else:
                    logger.error(f"{bm_code}指数数据不足，跳过计算惩罚值")
            # 5. 遍历所有跟踪的指数，喂入你的核心引擎
            update_penalty_data = []
            for code, fund_rates in history_map.items():
                if len(fund_rates) == 3:
                    # 第一步：计算超额波动
                    excess_vol = calc_daily_excess_volatility_batch(fund_rates, index_rates_list)
                    # 第二步：计算最终惩罚值
                    penalty = get_penalty(excess_vol)
                    # 组装批量更新元组
                    update_penalty_data.append((round(penalty, 4), code, trade_date))
            # 6. 批量更新惩罚值到数据库
            if update_penalty_data:
                index_daily_history.update_penalty_data(self.db, update_penalty_data)
                logger.info(f"成功调用核心引擎计算并更新 {len(update_penalty_data)} 个指数的惩罚值！")
        except Exception as e:
            logger.error(f"计算历史惩罚值失败: {e}")
            conn.rollback()
        finally:
            conn.close()

    def indices_handler(self, msgs):
        self._process_tick_msgs(msgs, 'index')

    def etf_handler(self, msgs):
        self._process_tick_msgs(msgs, 'etf')

    def _process_tick_msgs(self, msgs, record_type):
        """
        处理 xtdata 推送回来的指数数据，并拼装为批量入库格式
        """
        update_data = []

        for code in msgs:
            tick = msgs[code]

            try:
                # 1. 净化代码名称 (复用你现有的 utils)
                purified_code = helper.utils.purified_code(code)

                # 2. 提取并格式化交易日期 (将毫秒时间戳转为 YYYY-MM-DD)
                # 注意：如果遇到周末测试没有time字段，需做好容错
                timestamp_ms = tick.get('time', 0)
                if timestamp_ms == 0:
                    continue  # 无效数据跳过
                trade_date = datetime.datetime.fromtimestamp(timestamp_ms / 1000.0).strftime('%Y-%m-%d')
                if self.trade_date == "":
                    self.trade_date = trade_date

                # 3. 提取基础盘口数据
                close_price = tick.get('lastPrice', 0.0)
                pre_close = tick.get('lastClose', 0.0)
                open_price = tick.get('open', 0.0)
                high_price = tick.get('high', 0.0)
                low_price = tick.get('low', 0.0)
                volume = tick.get('volume', 0)
                amount = tick.get('amount', 0.0)

                # 4. 预计算核心风控参数：当日真实涨跌幅
                if pre_close and pre_close > 0:
                    vol_rate = round((close_price - pre_close) / pre_close, 4)
                else:
                    vol_rate = 0.0

                # 5. 组装为 tuple，严格对齐 batch_upsert_index_history 的参数顺序
                row_tuple = (purified_code, trade_date, close_price, pre_close, open_price, high_price, low_price, vol_rate, volume, amount, 'QMT')
                update_data.append(row_tuple)
                if not hasattr(self, 'success_indices_set') and record_type == 'index':
                    self.success_indices_set = set()
                if record_type == 'index':
                    # 注意：这里记录的是未净化的原始订阅 code，方便后续算差集
                    self.success_indices_set.add(code)

            except Exception as e:
                logger.error(f"处理数据 {code} 推送数据时解析异常: {e}")
                continue
        # 6. 调用批量更新函数入库
        if update_data:
            # 假设 self.db 是你的 pymysql connection
            index_daily_history.batch_upsert_index_history(self.db, update_data, record_type)
            logger.info(f"Batch upsert successful: {len(update_data)} {record_type} records")

    def parse_third_party_index_json(self, index_code, json_data, data_source):
        """
        解析第三方 (如东方财富) 的指数 JSON 数据，返回适配批量入库的格式

        :param json_data: dict, 第三方接口返回的完整 JSON 字典
        :return: list[tuple], 包含单条数据的列表，可直接传给 batch_upsert_index_history。解析失败返回空列表。
        """
        try:
            # 获取核心数据域
            data = json_data.get("data")
            if not data:
                logger.warning("第三方数据中未找到 'data' 字段")
                return []
            timestamp_sec = data.get("f86", 0)
            if timestamp_sec == 0:
                return []
            trade_date = datetime.datetime.fromtimestamp(timestamp_sec).strftime('%Y-%m-%d')

            # 3. 提取 OHLCV 数据 (注意：价格需要除以 100 还原真实小数)
            close_price = data.get("f43", 0) / 100.0  # 最新价
            high_price = data.get("f44", 0) / 100.0  # 最高价
            low_price = data.get("f45", 0) / 100.0  # 最低价
            open_price = data.get("f46", 0) / 100.0  # 开盘价
            pre_close = data.get("f60", 0) / 100.0  # 昨收价

            volume = data.get("f47", 0)  # 成交量
            amount = data.get("f48", 0.0)  # 成交额

            # 4. 计算当日核心风控参数：真实涨跌幅
            if pre_close and pre_close > 0:
                vol_rate = round((close_price - pre_close) / pre_close, 4)
            else:
                vol_rate = 0.0

            # 5. 严格对齐 batch_upsert_index_history 要求的字段顺序
            row_tuple = (index_code, trade_date, close_price, pre_close, open_price, high_price, low_price, vol_rate, volume, amount, data_source)

            # 包装成 List 返回，以便直接复用批量 Upsert 接口
            return [row_tuple]

        except Exception as e:
            logger.error(f"解析第三方指数数据失败: {e}, 原始数据: {json_data}")
            return []


