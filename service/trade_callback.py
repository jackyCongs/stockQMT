from xtquant.xttrader import XtQuantTraderCallback
import logging
from db import strategy_transaction
from db.db_pool import DBPool
from datetime import datetime
from service import account

logger = logging.getLogger(__name__)

class TradeCallback(XtQuantTraderCallback):

    def on_disconnected(self):
        """
        Connection disconnected
        :return:
        """
        logger.info("connection lost")

    # Order Details
    def on_stock_order(self, order):
        print("Order placement successful")
        # print(f"""
        # =============================
        #         Order Details
        # =============================
        # Account Type: {order.account_type},
        # Account ID: {order.account_id},
        # Stock Ticker: {order.stock_code},
        # Order ID: {order.order_id},
        # Counterpart Contract ID: {order.order_sysid},
        # Order Time: {order.order_time},
        # Order Type: {order.order_type},
        # Order Volume: {order.order_volume},
        # Price Type: {order.price_type},
        # Order Price: {order.price},
        # Traded Volume: {order.traded_volume},
        # Traded Avg Price: {order.traded_price},
        # Order Status: {order.order_status},
        # Status Msg: {order.status_msg},
        # Strategy Name: {order.strategy_name},
        # Order Remark: {order.order_remark},
        # Direction: {order.direction},
        # Offset Flag: {order.offset_flag}
        # """)

    def on_stock_trade(self, trade):
        logger.info(f"on_stock_trade executed, {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        platform_name = account.get_platform_name_by_account_id(trade.account_id)
        if platform_name is None:
            logger.error(f"Failed to retrieve platform_name: {trade.account_id}")
        logger.info(f"""
                =============================
                        Trade execution info
                =============================
                Broker Platform: {platform_name},
                Account Type: {trade.account_type},
                Account ID: {trade.account_id},
                Stock Ticker: {trade.stock_code},
                Order Type: {trade.order_type},
                Execution ID: {trade.traded_id},
                Execution Time: {trade.traded_time},
                Execution Avg Price: {trade.traded_price},
                Execution Volume: {trade.traded_volume},
                Execution Amount: {trade.traded_amount},
                Order ID: {trade.order_id},
                Counterpart Contract ID: {trade.order_sysid},
                Strategy Name: {trade.strategy_name},
                Order Remark: {trade.order_remark}
                """)
        logger.info(f"Execution update, {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} {trade.account_id}, {trade.stock_code}, {trade.order_id}")
        db = DBPool()
        strategy_transaction.add(db, trade, platform_name)
        logger.info(f"on_stock_trade execution processing complete, {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")

    def on_order_error(self, order_error):
        """
        Order placement failure callback
        :param order_error: XtOrderError object
        :return:
        """
        logger.info("on order_error callback")
        logger.info(f"Order error callback, {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}, {order_error.order_remark} {order_error.error_msg}")

    def on_cancel_error(self, cancel_error):
        """
        Order cancellation failure callback
        :param cancel_error: XtCancelError object
        :return:
        """
        logger.info("on cancel_error callback")
        logger.info(f"Order cancellation failed: {cancel_error.order_id}, {cancel_error.error_id}, {cancel_error.error_msg}")

    def on_order_stock_async_response(self, response):
        """
        Asynchronous order response callback
        :param response: XtOrderResponse object
        :return:
        """
        logger.info("on_order_stock_async_response callback")
        logger.info(f"Asynchronous order placement response: {response.account_id}, {response.order_id}, {response.seq}")

    def on_account_status(self, status):
        """
        :param status: XtAccountStatus object
        :return:
        """
        logger.info("on_account_status callback")
        logger.info(f"{status.account_id}, {status.account_type}, {status.status}")