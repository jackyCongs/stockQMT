import os
import json
import logging
from datetime import datetime
from decimal import Decimal
import paho.mqtt.client as mqtt
from xtquant import xtdata
from rich.live import Live
from rich.table import Table
from rich.text import Text


# 配置高性能日志（关掉 DEBUG，只保留警告和错误，节约盘中 I/O）
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("TickGateway")


class IndexMqGateway:
    def __init__(self, mq_host='127.0.0.1', mq_port=1883):
        self.mq_host = mq_host
        self.mq_port = mq_port
        self.mq_client = mqtt.Client(client_id="AlphaCore_PyGateway", clean_session=True)

        # 挂载回调函数（收发一体化核心）
        self.mq_client.on_connect = self._on_mq_connect
        self.mq_client.on_message = self._on_mq_message

        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.project_root = os.path.dirname(current_dir)
        self.config_path = os.path.join(self.project_root, 'files', 'alphacore_config.json')

        self.subscribe_list = []

        # 🟢 老哥要求的：维护最终指数计算结果的中央字典
        self.target_index_infos = {}
        self.latest_market_data = {}

    def _on_mq_connect(self, client, userdata, flags, rc):
        """MQTT 连接成功后的钩子"""
        if rc == 0:
            print(f"✅ 成功连接到 NanoMQ Broker: {self.mq_host}:{self.mq_port}")
            # 建立连接后，立刻向 NanoMQ 订阅 Golang 引擎算好的实时指数
            client.subscribe("alphacore/index/realtime", qos=0)
            print("📡 [全双工开启] 已挂载接收天线，监听主题: alphacore/index/realtime")
        else:
            logger.error(f"❌ 连接 NanoMQ 失败，返回码: {rc}")

    def _on_mq_message(self, client, userdata, message):
        try:
            results = json.loads(message.payload.decode('utf-8'))

            compatible_results = []
            for item in results:
                etf_code = item.get('i')
                iopv = item.get('iopv', 0.0)
                increase_rate = item.get('r', 0.0)  # 🟢 接收 Golang 传来的涨跌幅
                timestamp = item.get('t', 0)

                # 打印核对（直接在这里格式化为保留 6 位小数）
                # logger.info(f"✅ [核对] ETF: {etf_code} | 净值: 【 {iopv:.4f} 】 | 涨跌幅: 【 {increase_rate:.6f} 】")

                # 组装传给你原有的字典更新函数
                compatible_results.append({
                    "i": etf_code,
                    "p": iopv,  # p 存净值
                    "r": increase_rate,  # r 存涨跌幅
                    "t": timestamp
                })
                self.latest_market_data[etf_code] = {
                    "iopv": iopv,
                    "rate": increase_rate,
                    "update_time": timestamp
                }

            self._update_target_index_infos(compatible_results)

        except Exception as e:
            logger.error(f"处理 Golang 结果异常: {e}")

    def _update_target_index_infos(self, results):
        """将 Golang 算出的数据，完美洗入老哥指定的 target_index_infos 字典"""
        for res in results:
            code = res.get("i")
            current_price = res.get("p", 0.0)
            increase_rate = res.get("r", 0.0)
            time_ms = res.get("t", 0)

            if not code or time_ms == 0:
                continue

            pure_code = code

            # 如果字典里还没这个键，先初始化一个空字典
            if pure_code not in self.target_index_infos:
                self.target_index_infos[pure_code] = {}

            # 🟢 完美复刻老哥的数据结构
            self.target_index_infos[pure_code].update({
                'time': datetime.fromtimestamp(time_ms / 1000).strftime('%H:%M:%S'),
                'timestamp': time_ms / 1000,
                'start': 0,
                'current': round(current_price, 4),
                # Decimal 需要字符串传参以防浮点数精度丢失
                'increase_rate': Decimal(str(round(increase_rate, 6))),
                'status': True,
            })


    def connect_mq(self):
        """连接到边缘节点 NanoMQ"""
        try:
            # 开启 MQTT 的后台异步网络收发线程
            # loop_start 会在后台接管收发，我们的 _on_mq_message 就是被这个线程驱动的
            self.mq_client.connect(self.mq_host, self.mq_port, keepalive=60)
            self.mq_client.loop_start()
        except Exception as e:
            logger.error(f"❌ 连接 NanoMQ 失败，请检查服务是否启动: {e}")
            exit(1)

    def generate_table(self) -> Table:
        # 采用多列矩阵布局，极大提高屏幕利用率
        table = Table(title="🚀 AlphaCore 实时净值全景矩阵 (按代码首列向下排序)", style="blue", show_edge=False, show_lines=False)
        
        NUM_COLUMNS = 5  # 横向排 5 组 ETF
        import math
        
        for i in range(NUM_COLUMNS):
            table.add_column("代码", justify="center", style="cyan", no_wrap=True)
            table.add_column("净值", justify="right", style="bold magenta")
            table.add_column("涨跌幅", justify="right")
            if i < NUM_COLUMNS - 1:
                table.add_column("│", style="dim") # 组之间的间隔线

        sorted_codes = sorted(self.latest_market_data.keys())
        if not sorted_codes:
            table.add_row("等待数据接入...")
            return table
            
        # 计算需要的行数。比如 300个 / 5列 = 60行。
        # 采用“从上到下，再从左到右”的排列顺序，符合人类找字母顺序的直觉
        rows = math.ceil(len(sorted_codes) / NUM_COLUMNS)
        
        for r in range(rows):
            row_data = []
            for c in range(NUM_COLUMNS):
                idx = c * rows + r
                if idx < len(sorted_codes):
                    code = sorted_codes[idx]
                    data = self.latest_market_data[code]
                    iopv_str = f"{data['iopv']:.4f}"
                    rate = data['rate']
                    
                    # 格式化涨跌幅
                    if rate > 0:
                        rate_str = f"[red]+{rate * 100:.2f}%[/red]"
                    elif rate < 0:
                        rate_str = f"[green]{rate * 100:.2f}%[/green]"
                    else:
                        rate_str = f"[white]0.00%[/white]"
                        
                    row_data.extend([str(code), iopv_str, rate_str])
                else:
                    # 凑数补齐空位
                    row_data.extend(["", "", ""])
                    
                if c < NUM_COLUMNS - 1:
                    row_data.append("│")
                    
            table.add_row(*row_data)

        return table


    def load_subscription_list(self):
        """从 alphacore_config.json 中提取去重后的全量成分股名单"""
        if not os.path.exists(self.config_path):
            logger.error("未找到 alphacore_config.json！请先运行盘前初始化脚本。")
            exit(1)

        with open(self.config_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)

        all_stocks = set()
        for idx_code, config in payload.items():
            for stock_code in config.get("components", {}).keys():
                all_stocks.add(stock_code)

        self.subscribe_list = list(all_stocks)
        print(f"✅ 从配置文件加载完毕，今日需监听成分股总数: {len(self.subscribe_list)} 只")

    def on_whole_tick_callback(self, datas):
        """
        【发送总线】极限压榨性能：全推批量聚合回调
        """
        try:
            batch_payload = []
            for stock_code, tick_data in datas.items():
                if not tick_data:
                    continue

                batch_payload.append({
                    "c": stock_code,
                    "p": tick_data.get("lastPrice", 0),
                    "v": tick_data.get("volume", 0),
                    "a": tick_data.get("amount", 0),
                    "t": tick_data.get("time", 0)
                })

            if batch_payload:
                self.mq_client.publish(
                    topic="alphacore/tick/batch",
                    payload=json.dumps(batch_payload),
                    qos=0
                )
        except Exception as e:
            logger.error(f"批量打包 Tick 并转发 MQ 时异常: {e}")

    def start_gateway(self):
        """启动网关，接管 QMT 行情并永久阻塞"""
        self.connect_mq()
        self.load_subscription_list()

        print("🚀 开始向 QMT 内存总线注册【全推批处理】Tick 订阅...")

        xtdata.subscribe_whole_quote(
            code_list=self.subscribe_list,
            callback=self.on_whole_tick_callback
        )

        print("⚡ 极速行情网关已全线贯通！发送/接收双引擎全速运转中...")
        print("🛑 保持此进程存活... (按 Ctrl+C 终止)")

