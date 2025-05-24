from xtquant.xttrader import XtQuantTraderCallback
import logging
from db import strategy_transaction
from main import db as dbcon

logging.basicConfig(level=logging.INFO,
                    format='%(message)s',
                    filename='logs/app.log',
                    filemode='a')
logger = logging.getLogger(__name__)

class TradeCallback(XtQuantTraderCallback):

    def on_disconnected(self):
        """
        连接断开
        :return:
        """
        logger.info("connection lost")

    # 委托信息
    def on_stock_order(self, order):
        print("委托成功")
        # print(f"""
        # =============================
        #         委托信息
        # =============================
        # 账号类型: {order.account_type},
        # 资金账号: {order.account_id},
        # 证券代码: {order.stock_code},
        # 订单编号: {order.order_id},
        # 柜台合同编号: {order.order_sysid},
        # 报单时间: {order.order_time},
        # 委托类型: {order.order_type},
        # 委托数量: {order.order_volume},
        # 报价类型: {order.price_type},
        # 委托价格: {order.price},
        # 成交数量: {order.traded_volume},
        # 成交均价: {order.traded_price},
        # 委托状态: {order.order_status},
        # 委托状态描述: {order.status_msg},
        # 策略名称: {order.strategy_name},
        # 委托备注: {order.order_remark},
        # 多空方向: {order.direction},
        # 交易操作: {order.offset_flag}
        # """)

    def on_stock_trade(self, trade):
        print(f"""
                =============================
                        成交信息
                =============================
                账号类型: {trade.account_type},
                资金账号: {trade.account_id},
                证券代码: {trade.stock_code},
                委托类型: {trade.order_type},
                成交编号: {trade.traded_id},
                成交时间: {trade.traded_time},
                成交均价: {trade.traded_price},
                成交数量: {trade.traded_volume},
                成交金额: {trade.traded_amount},
                订单编号: {trade.order_id},
                柜台合同编号: {trade.order_sysid},
                策略名称: {trade.strategy_name},
                委托备注: {trade.order_remark}
                """)
        logger.info("on trade callback")
        logger.info(f"成交变动 {trade.account_id}, {trade.stock_code}, {trade.order_id}")
        strategy_transaction.add(dbcon, trade)

    def on_order_error(self, order_error):
        """
        委托失败推送
        :param order_error:XtOrderError 对象
        :return:
        """
        logger.info("on order_error callback")
        logger.info(f"委托报错回调 {order_error.order_remark} {order_error.error_msg}")

    def on_cancel_error(self, cancel_error):
        """
        撤单失败推送
        :param cancel_error: XtCancelError 对象
        :return:
        """
        logger.info("on cancel_error callback")
        logger.info(f"撤单失败 {cancel_error.order_id}, {cancel_error.error_id}, {cancel_error.error_msg}")

    def on_order_stock_async_response(self, response):
        """
        异步下单回报推送
        :param response: XtOrderResponse 对象
        :return:
        """
        logger.info("on_order_stock_async_response callback")
        logger.info(f"异步下单 {response.account_id}, {response.order_id}, {response.seq}")

    def on_account_status(self, status):
        """
        :param response: XtAccountStatus 对象
        :return:
        """
        logger.info("on_account_status callback")
        logger.info(f"{status.account_id}, {status.account_type}, {status.status}")