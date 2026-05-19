import os
import json
import logging
import paho.mqtt.client as mqtt
from helper import notifier
from xtquant import xtdata

# 配置高性能日志（关掉 DEBUG，只保留警告和错误，节约盘中 I/O）
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("TickGateway")


class IndexMqGateway:
    def __init__(self, mq_host='127.0.0.1', mq_port=1883):
        self.mq_host = mq_host
        self.mq_port = mq_port
        self.mq_client = mqtt.Client(client_id="AlphaCore_PyGateway", clean_session=True)

        # 获取我们盘前生成的 JSON 文件路径
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.project_root = os.path.dirname(current_dir)
        self.config_path = os.path.join(self.project_root, 'files', 'alphacore_config.json')

        self.subscribe_list = []

    def connect_mq(self):
        """连接到边缘节点 NanoMQ"""
        try:
            self.mq_client.connect(self.mq_host, self.mq_port, keepalive=60)
            self.mq_client.loop_start()  # 开启 MQTT 的后台异步网络收发线程
            print(f"✅ 成功连接到 NanoMQ Broker: {self.mq_host}:{self.mq_port}")
        except Exception as e:
            logger.error(f"❌ 连接 NanoMQ 失败，请检查服务是否启动: {e}")
            exit(1)

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
        【极限压榨性能：全推批量聚合回调】
        将同一瞬间推送过来的碎片化 Tick 打包成一个 JSON 数组，单次 I/O 发送！
        """
        try:
            batch_payload = []
            for stock_code, tick_data in datas.items():
                if not tick_data:
                    continue

                # 由于我们取消了按主题区分代码，这里必须把股票代码 "c" 塞进包里供 Golang 识别
                # p: 最新价, v: 成交量, a: 成交额, t: 时间戳
                batch_payload.append({
                    "c": stock_code,
                    "p": tick_data.get("lastPrice", 0),
                    "v": tick_data.get("volume", 0),
                    "a": tick_data.get("amount", 0),
                    "t": tick_data.get("time", 0)
                })

            # 如果数组不为空，执行【一发入魂】
            if batch_payload:
                self.mq_client.publish(
                    topic="alphacore/tick/batch",  # 统一合并到一个总线主题
                    payload=json.dumps(batch_payload),
                    qos=0
                )
        except Exception as e:
            # 盘中尽量不要 print，用 logger 记录错误
            logger.error(f"批量打包 Tick 并转发 MQ 时异常: {e}")
            notifier.send_telegram_alert("报警", f"打包 Tick 并转发 MQ 时异常, on_whole_tick_callback发生错误: {str(e)[:200]},\n请立即处理")

    def start_gateway(self):
        """启动网关，接管 QMT 行情并永久阻塞"""
        self.connect_mq()
        self.load_subscription_list()

        print("🚀 开始向 QMT 内存总线注册【全推批处理】Tick 订阅...")

        # 【核心架构升级】：直接把所有去重代码列表扔进去，建立唯一监听总线
        xtdata.subscribe_whole_quote(
            code_list=self.subscribe_list,
            callback=self.on_whole_tick_callback
        )

        print("⚡ 极速行情网关已全线贯通！正在源源不断地向 NanoMQ 泵入实时聚合数据...")
        xtdata.run()