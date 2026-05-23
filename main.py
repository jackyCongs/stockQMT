# coding=utf-8
import sys
import time
import traceback
import logging
import argparse
from xtquant import xtdata
from db.db_pool import DBPool
from service.stock_service import StockService
from service.trader_service import TraderService
from service.trans_flows import TransFlows
from service.etf_alphacore_config_service import ETFAlphaCoreConfigService
from service.index_calculator_service import IndexReplicationCalculator
from service.index_nanomq_publisher import IndexMqGateway
from strategies.strategy1 import Strategy1
from strategies.strategy2 import Strategy2
from helper.time_utils import get_time
from helper import log_utils
import helper.data_loader as data_loader

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
fund_spider_cookie = 'qgqp_b_id=cc1897aac4f07c77d00260f5336e636a; st_nvi=n_ig8DMmih7vyVepFV-rO3463; nid=0bbb6ef76661f2ea8518b074ed10795c; nid_create_time=1759108160426; gvi=9kBn8gog09BOvw6oxTZb0c044; gvi_create_time=1759108160426; mtp=1; ct=qXJ7p_b0tTxbdhbEsUUII8tSAkioGY0X09xiBqdk_PQ3SAw7KWTz9k5D_hF-xTv2zzKJRvXFgddwTulWw0Xe74I4Jlj0a7Pjo6AT5K1kQdmcIN-IjI4UdbkQNUdXMl05NLDi3njE-bKXE0jgv-36l6QAqFfhZY3fYEqk-C38O1k; ut=FobyicMgeV54OLFNgnrRk6tRIfhkpfmmwhXqBsHsreHL1TS1BzgeJDLlFQyscSLDQ89gDk2aAxV5CaneW33dw4X5AotnDYGGvjcsLpQwIwCfb-EaelfUTiA4XWeS9ToOybaxJP0HDV7tF8nbuvevQsRPFl3en81vU8xtyOlJuHOrRSkuhzxJbwzgXYsBQ1-b-q2VGk5WnlZFeqnADZfgjrJh-7dTy2ZTlG3bYh6bk5WiEQCB8TvBGt9TOP0FtGIEYvzgHXQcHghsDPu6xPDQUH9nNZ7LIj8G; pi=1240037623276744%3Bi1240037623276744%3B%E8%82%A1%E5%8F%8B99dc898166%3Bloj2Lv%2FD%2F9Ct55aoQaElgt%2FG%2FPTNM2HbafQLw47mrkmf6cyoW4rA9npFHgkbiB2QoE%2F%2FZEkoMpPjkZARUYRam1X3kx85HJFZ7E55ZzEIA1Yh7yUUY4ZL8R3Xnj7lVaCcmkvPYS1jPDkiz2nT%2FaB%2FhxWmHUZoh%2BUkjg8eQl%2B3URJ3yKjmzV%2BGWpcZK4sP3DoMld2LxoQ1%3BMyrVmvvD6DCd546fiYpL1yRVRNd71eOI2H%2FNKkrSjMl40a8Ft24uwJyYUDcPbHx3zn%2FxMpahW6pfkRrSVOj6QbZ2x4mxLMQ9sRebIhiKSKgeb3Tt2Qm0CbPyl%2BHXZ8wU75dKogbb%2FYUFZer036vHPdLYJlMtrw%3D%3D; uidal=1240037623276744%e8%82%a1%e5%8f%8b99dc898166; sid=; vtpst=|; st_pvi=53448496909725; st_sp=2025-03-08%2022%3A34%3A17; st_inirUrl=https%3A%2F%2Ffund.eastmoney.com%2F160630.html'

if __name__ == '__main__':
    strategy_map = {
        "s1": Strategy1,
        "s2": Strategy2,
    }
    parser = argparse.ArgumentParser(description="StockQMT: Quantitative Trading System Runner")
    # param：-mode
    parser.add_argument("-mode", type=str, required=True, choices = ["0", "1", "2", "3", "4"],  help="Execution mode: 0: Trading, 1: Accounting, 2: UPDATE Market Data")
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
            strategy_class(db, trader_service, args.platform, fund_spider_cookie).run()
            trader_service.xt_trader.run_forever()
        # mode 1: accounting
        elif args.mode == '1':
            trader_service = TraderService(session_id, args.platform)
            logger.info("Executing portfolio accounting...")
            TransFlows(trader_service.get_asset(), args.platform).run()
            logger.info("Accounting process completed.")
        # mode 2: update stock price
        elif args.mode == '2':
            stock_service = StockService(db)
            logger.info("Updating market data...")
            stock_service.update_stock_price()
            logger.info("Market data update completed...")
            time.sleep(2)
            logger.info("Updating index data...")
            stock_service.update_index_daily_history(fund_spider_cookie)
            logger.info("Index data update completed...")
        elif args.mode == '3':
            # 第一阶段：智能同步最新权重文件
            logger.info(">>> 阶段一：执行权重文件同步...")
            ETFAlphaCoreConfigService(db).run(['530100', '159810'])

            exit()

            # 第二阶段：计算虚拟股数并强制对齐入库
            logger.info(">>> 阶段二：提取复权基准、计算虚拟股数并入库...")
            calculator = IndexReplicationCalculator(db_pool=db, base_capital=1000000000)
            yesterday_date = data_loader.get_previous_date()
            calculator.run_daily_pipeline(yesterday_date)

            logger.info("=== [AlphaCore] 盘前流水线全部执行完毕，弹药已上膛 ===")
        elif args.mode == '4':
            gateway = IndexMqGateway(mq_host='127.0.0.1', mq_port=1883)
            gateway.start_gateway()
            time.sleep(10)
            #logger.info(gateway.target_index_infos)
            xtdata.run()
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