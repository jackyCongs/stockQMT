import sys
import os
import json
import time
import logging
from datetime import datetime
from decimal import Decimal
import paho.mqtt.client as mqtt
from xtquant import xtdata
from service.watchdog_service import WatchdogService

# 注入并导入通达信 TQ 插件路径
tdx_path = r"D:\stock_software\tongdaxin\PYPlugins\user"
if tdx_path not in sys.path:
    sys.path.append(tdx_path)

try:
    from tqcenter import tq as _tq
except ImportError:
    _tq = None


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
        self.bse_subscribe_list = []
        self.realtime_iopv_infos = {}
        self.etf_to_index_code = {}
        self.watchdog = WatchdogService()

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
                increase_rate = item.get('r', 0.0)
                timestamp = item.get('t', 0)
                compatible_results.append({
                    "i": etf_code,
                    "p": iopv,  # p 存净值
                    "r": increase_rate,  # r 存涨跌幅
                    "t": timestamp
                })
            self._update_realtime_iopv_infos(compatible_results)

        except Exception as e:
            logger.error(f"处理 Golang 结果异常: {e}")

    def _update_realtime_iopv_infos(self, results):
        for res in results:
            code = res.get("i")
            current_price = res.get("p", 0.0)
            increase_rate = res.get("r", 0.0)
            time_ms = res.get("t", 0)

            if not code or time_ms == 0:
                continue

            pure_code = code
            # 如果字典里还没这个键，先初始化一个空字典
            if pure_code not in self.realtime_iopv_infos:
                self.realtime_iopv_infos[pure_code] = {}

            index_code = self.etf_to_index_code.get(pure_code, '')
            if index_code == '':
                logger.warning(f"etf_code: {pure_code}, 对应的index_code缺失: {index_code}")

            self.realtime_iopv_infos[pure_code].update({
                'time': datetime.fromtimestamp(time_ms / 1000).strftime('%H:%M:%S'),
                'timestamp': time_ms / 1000,
                'start': 0,
                'current': round(current_price, 6),
                'increase_rate': (round(increase_rate, 6)),
                'index_code': index_code,
            })


    def connect_mq(self):
        try:
            self.mq_client.connect(self.mq_host, self.mq_port, keepalive=60)
            self.mq_client.loop_start()
        except Exception as e:
            logger.error(f"❌ 连接 NanoMQ 失败，请检查服务是否启动: {e}")
            exit(1)


    def load_subscription_list(self):
        if not os.path.exists(self.config_path):
            logger.error("未找到 alphacore_config.json！请先运行盘前初始化脚本。")
            exit(1)

        with open(self.config_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)

        all_stocks = set()
        bse_stocks = set()
        for etf_code, config in payload.items():
            index_code = config.get("index_code", "")
            self.etf_to_index_code[etf_code] = index_code
            for stock_code in config.get("components", {}).keys():
                if stock_code.endswith('.BJ'):
                    bse_stocks.add(stock_code)
                else:
                    all_stocks.add(stock_code)

        self.subscribe_list = list(all_stocks)
        self.bse_subscribe_list = list(bse_stocks)
        print(f"✅ 从配置文件加载完毕，QMT 订阅成分股: {len(self.subscribe_list)} 只")
        print(f"✅ 北交所(通达信TQ)订阅成分股: {len(self.bse_subscribe_list)} 只 → {self.bse_subscribe_list}")

    def on_whole_tick_callback(self, datas):
        try:
            self.watchdog.feed("publisher_qmt_tick")
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

    def _on_bse_hq_callback(self, data_str):
        """通达信 TQ 行情更新回调 - 收到推送后获取快照并转发 MQ"""
        try:
            self.watchdog.feed("publisher_bse_tick")
            code_info = json.loads(data_str)
            stock_code = code_info.get('Code', '')
            if not stock_code:
                return

            if not _tq:
                logger.error("通达信 TQ SDK 未成功加载！")
                return
            snapshot = _tq.get_market_snapshot(stock_code=stock_code, field_list=[])
            if not snapshot:
                return

            now_ms = int(time.time() * 1000)
            batch_payload = [{
                "c": stock_code,
                "p": float(snapshot.get("Now", 0)),
                "v": int(snapshot.get("Volume", 0)),
                "a": float(snapshot.get("Amount", 0)),
                "t": now_ms
            }]
            self.mq_client.publish(
                topic="alphacore/tick/batch",
                payload=json.dumps(batch_payload),
                qos=0
            )
        except Exception as e:
            logger.error(f"处理北交所 TQ 行情回调异常: {e}")

    def _start_bse_subscription(self):
        """初始化通达信 TQ 并订阅北交所股票"""
        if not _tq:
            raise ImportError("无法载入通达信 tqcenter 模块，请检查通达信是否安装或路径是否正确！")
        _tq.initialize(__file__)
        print("✅ 通达信 TQ SDK 初始化完成")

        result = _tq.subscribe_hq(
            stock_list=self.bse_subscribe_list,
            callback=self._on_bse_hq_callback
        )
        print(f"🏛️ 北交所 TQ 订阅结果: {result}")

    def start_gateway(self):
        """启动网关，接管 QMT + TQ 行情并永久阻塞"""
        self.connect_mq()
        self.load_subscription_list()

        # ① 启动北交所 TQ 行情订阅
        if self.bse_subscribe_list:
            self._start_bse_subscription()
            self.watchdog.register("publisher_bse_tick", 30, "行情网关-北交所TQ行情")

        # ② 其余股票走 QMT 全推
        if self.subscribe_list:
            print("🚀 开始向 QMT 内存总线注册【全推批处理】Tick 订阅...")
            xtdata.subscribe_whole_quote(
                code_list=self.subscribe_list,
                callback=self.on_whole_tick_callback
            )
            self.watchdog.register("publisher_qmt_tick", 30, "行情网关-QMT全推行情")

        # 启动看门狗（单例，如果已启动不会重复启动）
        self.watchdog.start()

        print("⚡ 极速行情网关已全线贯通！QMT + TQ 双引擎全速运转中...")
        print("🐶 看门狗已挂载，QMT/北交所行情中断将自动报警")
        print("🛑 保持此进程存活... (按 Ctrl+C 终止)")

