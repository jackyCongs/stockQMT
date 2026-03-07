# coding=utf-8
import sys
import traceback
import logging
import argparse
from xtquant import xtdata
from db.db_pool import DBPool
from service.stock_service import StockService
from service.trader_service import TraderService
from service.trans_flows import TransFlows
from strategies.strategy1 import Strategy1
from strategies.strategy2 import Strategy2
from helper.time_utils import get_time
from helper import log_utils

log_format = '%(asctime)s | %(levelname)s | %(name)s.%(funcName)s:%(lineno)d | %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, filename='logs/app.log', encoding='utf-8', filemode='a') #, force=True
logger = logging.getLogger(__name__)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter(log_format))
logging.getLogger().addHandler(console_handler)
# 全局变量
xtdata.enable_hello = False
db = DBPool()
# 交易服务
trader_service = None
session_id = round(get_time())

if __name__ == '__main__':
    strategy_map = {
        "s1": Strategy1,
        "s2": Strategy2,
    }
    parser = argparse.ArgumentParser(description="StockQMT: Quantitative Trading System Runner")
    # param：-mode
    parser.add_argument("-mode", type=str, required=True, choices = ["0", "1", "2"],  help="Execution mode: 0: Trading, 1: Accounting, 2: UPDATE Market Data")
    # param：-platform
    parser.add_argument("-platform", type=str, choices=["大同证券", "湘财证券"], help="Target trading platform: 大同证券, 湘财证券")
    # param: -strategy
    parser.add_argument("-s", type=str, choices=["s1", "s2"], help="Strategy ID to execute (e.g., s1, s2)")
    args = parser.parse_args()
    logger = log_utils.init_logging(args.s, args.mode)
    if args.mode in ["0", "1"] and not args.platform:
        parser.error("The -platform argument is required when -mode is 0 or 1.")
    if args.mode in ["0"] and not args.s:
        parser.error("The -s argument is required when -mode is 0.")

    try:
        # mode 0: trading
        if args.mode == "0":
            trader_service = TraderService(session_id, args.platform)
            if args.s not in strategy_map:
                parser.error(f"Undefined strategy ID: {args.mode}")
            logger.info(f"Starting strategy: {args.s}")
            strategy_class = strategy_map[args.s]
            strategy_class(db, trader_service, args.platform).run()
            trader_service.xt_trader.run_forever()
        # mode 1: accounting
        elif args.mode == '1':
            trader_service = TraderService(session_id, args.platform)
            logger.info("Executing portfolio accounting...")
            TransFlows(trader_service.get_asset(), args.platform).run()
            logger.info("Accounting process completed.")
        # mode 2: update stock price
        elif args.mode == '2':
            logger.info("Updating market data...")
            StockService(db).update_stock_price()
            logger.info("Market data update completed.")
        else:
            logger.info(f"Unknown execution mode: {args.mode}")
    except Exception as e:
        logger.info(f"An exception occurred：{e}")
        traceback.print_exc()
    finally:
        logger.info("Closing system resources...")
        db.close()
        if trader_service is not None:
            trader_service.xt_trader.stop()
        logger.info("System shutdown complete.")