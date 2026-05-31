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

    def maintain_sh_etfs(self):
        sh_fund_codes = xtdata.get_stock_list_in_sector('沪深基金')

        print(f"共{len(sh_fund_codes)}个基金")
        
        if not sh_fund_codes:
            logger.error("从QMT未能获取到'沪深基金'列表，请检查QMT终端是否已开启并正常连接。")
            return
            
        # 过滤出代码以5开头并且是上交所(.SH)的，并查询名称
        sh_etfs = {}
        for full_code in sh_fund_codes:
            # full_code 形如 "510050.SH"
            if not full_code.endswith('.SH'):
                continue
                
            code = full_code.split('.')[0]
            # 严格过滤出真正的 ETF：
            # 上交所 ETF 主要以 51, 52, 53, 56, 58 开头 (501, 502, 505 等为 LOF 或封闭式基金)
            # 另外排除 519 开头的传统场外开放式基金（场内只做申赎，无PCF清单）
            if str(code).startswith(('51', '52', '53', '56', '58')) and not str(code).startswith('519'):
                detail = xtdata.get_instrument_detail(full_code)
                name = detail.get('InstrumentName', code) if detail else code
                
                # 双重保险：如果名称里明确写了 LOF，也坚决排除
                if 'LOF' not in str(name).upper():
                    sh_etfs[code] = name
                
        logger.info(f"QMT共获取到 {len(sh_etfs)} 个上交所纯正场内ETF")
        
        conn = self.db.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT code, status, remark FROM stock WHERE source = 'A' AND is_etf = 1 AND is_inner_etf = 1 and inner_etf_type='etf'")
                existing = cursor.fetchall()
                existing_map = {}
                if existing:
                    if isinstance(existing[0], dict):
                        existing_map = {row['code']: {'status': row['status'], 'remark': row.get('remark') or ''} for row in existing}
                    else:
                        existing_map = {row[0]: {'status': row[1], 'remark': row[2] if len(row)>2 and row[2] else ''} for row in existing}
                
                new_codes = []
                for code, name in sh_etfs.items():
                    if code not in existing_map:
                        now = datetime.datetime.now()
                        new_codes.append((name, code, 1, 'A', 1, 1, 'etf', 0.5, now, now))
                
                offline_codes = []
                warn_codes = []
                import time
                today_int = int(time.strftime('%Y%m%d'))
                
                for code, info in existing_map.items():
                    status = info['status']
                    if str(code).startswith('5') and code not in sh_etfs and status == 1:
                        # 不武断下线，调用底层详情接口做二次核实
                        full_code = f"{code}.SH"
                        detail = xtdata.get_instrument_detail(full_code)
                        
                        if not detail:
                            offline_codes.append((f"ETF彻底摘牌下线", code))
                        else:
                            expire_date_raw = detail.get('ExpireDate', 99999999)
                            try:
                                expire_date = int(expire_date_raw) if expire_date_raw else 99999999
                            except ValueError:
                                expire_date = 99999999
                                
                            if 0 < expire_date <= today_int:
                                offline_codes.append((f"ETF已退市(退市日:{expire_date})", code))
                            else:
                                warn_codes.append((f"疑似异常:不在沪深基金板块中", code))
                        
                online_codes = []
                clear_warn_codes = []
                for code, info in existing_map.items():
                    status = info['status']
                    remark = info['remark']
                    if str(code).startswith('5') and code in sh_etfs:
                        if status != 1 and status != -100:  # Covers status=0, status=-1 etc, but ignore -100
                            online_codes.append(code)
                        elif "疑似异常" in remark:
                            clear_warn_codes.append(code)
                        
                if new_codes:
                    insert_sql = """
                        INSERT INTO stock (name, code, status, source, is_etf, is_inner_etf, inner_etf_type, withdraw_commission_7rate, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    cursor.executemany(insert_sql, new_codes)
                    logger.info(f"成功新增插入了 {len(new_codes)} 个新的上交所ETF")
                    
                if offline_codes:
                    update_sql = "UPDATE stock SET status = 0, remark = %s, updated_at = NOW() WHERE code = %s"
                    cursor.executemany(update_sql, offline_codes)
                    logger.info(f"将 {len(offline_codes)} 个已明确退市或摘牌的上交所ETF标记为 status=0")
                    
                if warn_codes:
                    warn_sql = "UPDATE stock SET remark = %s, updated_at = NOW() WHERE code = %s"
                    cursor.executemany(warn_sql, warn_codes)
                    logger.warning(f"发现 {len(warn_codes)} 个在库ETF未出现在板块列表中，已写入remark警告。")
                    
                if online_codes:
                    update_online_sql = "UPDATE stock SET status = 1, remark = 'ETF重新上线', updated_at = NOW() WHERE code = %s"
                    cursor.executemany(update_online_sql, [(code,) for code in online_codes])
                    logger.info(f"将 {len(online_codes)} 个重新上线(包含status=-1等)的上交所ETF标记为 status=1")
                    
                if clear_warn_codes:
                    clear_sql = "UPDATE stock SET remark = NULL, updated_at = NOW() WHERE code = %s"
                    cursor.executemany(clear_sql, [(code,) for code in clear_warn_codes])
                    logger.info(f"成功清除了 {len(clear_warn_codes)} 个恢复正常的ETF异常警告备注")
                    
                if not new_codes and not offline_codes and not online_codes and not warn_codes and not clear_warn_codes:
                    logger.info("数据库已是最新，无需维护更新")
                    
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"维护上交所ETF数据失败: {e}")
        finally:
            conn.close()

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
        优化：取波动最大的两天计算，过滤掉波动最小的一天
        支持新加入的标的（数据不足3天时有多少算多少）
        """
        conn = self.db.get_connection()
        try:
            rows = index_daily_history.get_3_days_history_list(self.db, trade_date)
            if rows is None:
                logger.warning("无历史数据，跳过惩罚值计算")
                return
            # 3. 在内存中将数据按指数代码分组整理
            # 格式: {'000001.SH': [0.001, -0.002, 0.005], ...}
            history_map = defaultdict(list)
            for row in rows:
                code, t_date, amplitude = row
                history_map[code].append(float(amplitude))
            # 4. 提取基准指数的波动率列表 (构建 index_rates_list)
            # 假设你的三大宽基指数是这三个，如果没有数据则跳过
            benchmarks = ['000001', '399001', '000300']
            index_rates_list = []
            for bm_code in benchmarks:
                if bm_code in history_map and len(history_map[bm_code]) >= 1:
                    index_rates_list.append(history_map[bm_code])
                else:
                    logger.warning(f"{bm_code}指数数据不足，跳过该基准")
            # 5. 遍历所有跟踪的指数，喂入你的核心引擎
            update_penalty_data = []
            for code, fund_rates in history_map.items():
                if len(fund_rates) >= 1:
                    # 第一步：计算超额波动（支持1~3天，取波动最大的两天）
                    excess_vol = calc_daily_excess_volatility_batch(fund_rates, index_rates_list)
                    # 第二步：计算最终惩罚值
                    penalty = get_penalty(excess_vol)
                    # 组装批量更新元组
                    update_penalty_data.append((round(penalty, 4), code, trade_date))
                    if len(fund_rates) < 3:
                        logger.info(f"[{code}] 仅有 {len(fund_rates)} 天数据，仍参与惩罚值计算")
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


