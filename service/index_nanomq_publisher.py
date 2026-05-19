import os
import json
import logging
import paho.mqtt.client as mqtt
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

    def on_tick_callback(self, data):
        """
        【极其核心：极速回调函数】
        QMT 推送过来的格式: {'600519.SH': [{'time': 1716200000000, 'lastPrice': 1700.5, ...}]}
        这里的代码必须极度精简，绝对不能有任何 DB 操作或 time.sleep！
        """
        try:
            for stock_code, tick_list in data.items():
                if not tick_list:
                    continue

                # 通常只取最新的一笔 Tick
                latest_tick = tick_list[-1]

                # -------------------------------------------------------------
                # 【带宽压榨优化】：为了防止 4000 只股票把 NanoMQ 塞满
                # 我们必须把长单词缩短，只保留 Golang 算点位必备的最小字段！
                # p: 最新价, v: 成交量, a: 成交额, t: 时间戳
                # -------------------------------------------------------------
                mini_payload = json.dumps({
                    "p": latest_tick.get("lastPrice", 0),
                    "v": latest_tick.get("volume", 0),
                    "a": latest_tick.get("amount", 0),
                    "t": latest_tick.get("time", 0)
                })

                # 发布到 NanoMQ，主题格式设为: alphacore/tick/600519.SH
                # 使用 qos=0 (最多交付一次)，追求极致的低延迟，不在乎偶尔丢一个包
                self.mq_client.publish(
                    topic=f"alphacore/tick/{stock_code}",
                    payload=mini_payload,
                    qos=0
                )
        except Exception as e:
            # 盘中尽量不要 print，用 logger 记录错误
            logger.error(f"处理 Tick 并转发 MQ 时异常: {e}")

    def start_gateway(self):
        """启动网关，接管 QMT 行情并永久阻塞"""
        self.connect_mq()
        self.load_subscription_list()

        print("🚀 开始向 QMT 内存总线注册 Tick 订阅...")
        # 不需要分批了，因为这里不下载历史，只是内存挂钩子
        for qmt_code in self.subscribe_list:
            xtdata.subscribe_quote(
                stock_code=qmt_code,
                period='tick',
                count=0,
                callback=self.on_tick_callback  # <=== 把我们写好的极速回调挂上去！
            )

        print("⚡ 行情网关已全线贯通！正在源源不断地向 NanoMQ 泵入实时数据...")
        print("🛑 保持此进程存活... (按 Ctrl+C 终止)")

        # 必须调用 xtdata.run()，让主线程永久阻塞，否则 Python 脚本瞬间就结束了！
        xtdata.run()


if __name__ == '__main__':
    gateway = QmtNanoMqGateway(mq_host='127.0.0.1', mq_port=1883)
    gateway.start_gateway()