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

# Inject and import TongDaXin TQ plugin path
tdx_path = r"D:\stock_software\tongdaxin\PYPlugins\user"
if tdx_path not in sys.path:
    sys.path.append(tdx_path)

try:
    from tqcenter import tq as _tq
except ImportError:
    _tq = None


# Configure high-performance logging (disable DEBUG, retain WARNING/ERROR to save I/O overhead)
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("TickGateway")


class IndexMqGateway:
    def __init__(self, mq_host='127.0.0.1', mq_port=1883):
        self.mq_host = mq_host
        self.mq_port = mq_port
        self.mq_client = mqtt.Client(client_id="AlphaCore_PyGateway", clean_session=True)

        # Bind callback functions (core of the transceiver integration)
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
        """Callback triggered after successful connection to MQTT broker"""
        if rc == 0:
            print(f"✅ Successfully connected to NanoMQ Broker: {self.mq_host}:{self.mq_port}")
            # Immediately subscribe to real-time index calculations calculated by the Golang engine
            client.subscribe("alphacore/index/realtime", qos=0)
            print("📡 [Full Duplex Open] Listening to topic: alphacore/index/realtime")
        else:
            logger.error(f"❌ Failed to connect to NanoMQ, return code: {rc}")

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
                    "p": iopv,  # 'p' stores net asset value (IOPV)
                    "r": increase_rate,  # 'r' stores percentage price change
                    "t": timestamp
                })
            self._update_realtime_iopv_infos(compatible_results)

        except Exception as e:
            logger.error(f"Exception while processing Golang payload: {e}")

    def _update_realtime_iopv_infos(self, results):
        for res in results:
            code = res.get("i")
            current_price = res.get("p", 0.0)
            increase_rate = res.get("r", 0.0)
            time_ms = res.get("t", 0)

            if not code or time_ms == 0:
                continue

            pure_code = code
            # Initialize an empty dictionary if the key does not exist
            if pure_code not in self.realtime_iopv_infos:
                self.realtime_iopv_infos[pure_code] = {}

            index_code = self.etf_to_index_code.get(pure_code, '')
            if index_code == '':
                logger.warning(f"etf_code: {pure_code}, missing corresponding index_code: {index_code}")

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
            logger.error(f"❌ Failed to connect to NanoMQ. Please verify if the service is running: {e}")
            exit(1)


    def load_subscription_list(self):
        if not os.path.exists(self.config_path):
            logger.error("alphacore_config.json not found! Please run the pre-market initialization script first.")
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
        print(f"✅ Loaded from configuration. QMT subscribed components: {len(self.subscribe_list)}")
        print(f"✅ BSE (TDX TQ) subscribed components: {len(self.bse_subscribe_list)} -> {self.bse_subscribe_list}")

    def on_whole_tick_callback(self, datas):
        try:
            self.watchdog.feed("publisher_qmt_tick")
            batch_payload = []
            for stock_code, tick_data in datas.items():
                if not tick_data:
                    continue

                price = tick_data.get("lastPrice", 0)
                if price <= 0:
                    price = tick_data.get("lastClose", 0)
                    if price <= 0:
                        logger.critical(
                            f"[FATAL] QMT feed {stock_code} real-time price and previous close are both 0. Data source anomaly! Terminating process immediately!"
                        )
                        self.mq_client.disconnect()
                        os._exit(1)
                    else:
                        logger.warning(
                            f"[Fallback] QMT feed {stock_code} real-time price is 0. Defaulting to previous close: {price}"
                        )

                batch_payload.append({
                    "c": stock_code,
                    "p": price,
                    "v": tick_data.get("volume", 0),
                    "a": tick_data.get("amount", 0),
                    "t": tick_data.get("time", 0)
                })

            if batch_payload:
                now = datetime.now()
                if now.hour == 9 and 25 <= now.minute <= 29:
                    logger.info(f"NMQ payload at {now.strftime('%H:%M:%S')}: {json.dumps(batch_payload, ensure_ascii=False)}")
                self.mq_client.publish(
                    topic="alphacore/tick/batch",
                    payload=json.dumps(batch_payload),
                    qos=0
                )
        except Exception as e:
            logger.error(f"Exception while batching ticks and forwarding to MQ: {e}")

    def _on_bse_hq_callback(self, data_str):
        """TDX TQ quote update callback - fetch snapshot and forward to MQ upon push message"""
        try:
            code_info = json.loads(data_str)
            stock_code = code_info.get('Code', '')
            if not stock_code:
                return

            if not _tq:
                logger.error("TDX TQ SDK not loaded successfully!")
                return
            snapshot = _tq.get_market_snapshot(stock_code=stock_code, field_list=[])
            if not snapshot:
                return
            self.watchdog.feed(f"publisher_bse_tick_{stock_code}")

            price = float(snapshot.get("Now", 0))
            if price <= 0:
                price = float(snapshot.get("LastClose", 0))
                if price <= 0:
                    logger.critical(
                        f"[FATAL] BSE TQ feed {stock_code} real-time price and previous close are both 0. Data source anomaly! Terminating process immediately!"
                    )
                    self.mq_client.disconnect()
                    os._exit(1)
                else:
                    pass
                    # logger.warning(
                    #     f"[Fallback] BSE TQ feed {stock_code} real-time price is 0. Defaulting to previous close: {price}"
                    # )

            now_ms = int(time.time() * 1000)
            batch_payload = [{
                "c": stock_code,
                "p": price,
                "v": int(snapshot.get("Volume", 0)),
                "a": float(snapshot.get("Amount", 0)),
                "t": now_ms
            }]
            if batch_payload:
                now = datetime.now()
                if now.hour == 9 and 25 <= now.minute <= 29:
                    pass
                    # logger.info(f"NMQ payload at {now.strftime('%H:%M:%S')}: {json.dumps(batch_payload, ensure_ascii=False)}")
                self.mq_client.publish(
                    topic="alphacore/tick/batch",
                    payload=json.dumps(batch_payload),
                    qos=0
                )
        except Exception as e:
            logger.error(f"Exception while processing BSE TQ feed callback: {e}")

    def _start_bse_subscription(self):
        """Initialize TDX TQ and subscribe to BSE equities"""
        if not _tq:
            raise ImportError("Cannot import TDX tqcenter module. Verify if TongDaXin is installed and path is configured correctly!")
        _tq.initialize(__file__)
        print("✅ TDX TQ SDK initialized successfully")

        result = _tq.subscribe_hq(
            stock_list=self.bse_subscribe_list,
            callback=self._on_bse_hq_callback
        )
        print(f"🏛️ BSE TQ subscription result: {result}")

    def start_gateway(self):
        """Start gateway, bind QMT + TQ feeds, and block main thread permanently"""
        self.connect_mq()
        self.load_subscription_list()

        # ① 1. Subscribe to BSE TQ quotes
        if self.bse_subscribe_list:
            self._start_bse_subscription()
            for stock_code in self.bse_subscribe_list:
                self.watchdog.register(f"publisher_bse_tick_{stock_code}", 30, f"行情网关-北交所TQ-{stock_code}")


        # ② 2. Other equities utilize QMT full quote push
        if self.subscribe_list:
            print("🚀 Registering QMT batch tick subscription on memory bus...")
            xtdata.subscribe_whole_quote(
                code_list=self.subscribe_list,
                callback=self.on_whole_tick_callback
            )
            self.watchdog.register("publisher_qmt_tick", 30, "QuoteGateway-QMT full quote push")

        # Start watchdog service (Singleton; does not duplicate if already running)
        self.watchdog.start()

        print("⚡ High-speed Quote Gateway operational! QMT + TQ dual engines running...")
        print("🐶 Watchdog active. Quote interruptions will trigger automated alerts.")
        print("🛑 Keeping process alive... (Press Ctrl+C to terminate)")
