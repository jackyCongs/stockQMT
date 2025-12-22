# coding=utf-8
import time
import traceback
from xtquant import xtdata
from db.db_pool import DBPool
from service import Trader_service, Trans_flows, Stock_service
import logging
from strategies import  Strategy1, Strategy2
import sys
from helper.time_utils import get_time

logging.basicConfig(level=logging.INFO,
                    format='%(message)s',
                    filename='logs/app.log',
                    filemode='a')
logger = logging.getLogger(__name__)

# 全局变量
xtdata.enable_hello = False
db = DBPool()
# 交易服务
traderService = None
session_id = round(get_time())


if __name__ == '__main__':
    db.initialize_pool()

    traderService = Trader_service.Trader_service(session_id)
    if len(sys.argv) > 1 and int(sys.argv[1]) == 1:
        Trans_flows.Trans_flows(traderService.get_asset()).run()
        print("accounting done")
        time.sleep(3)
        Stock_service.Stock_service(db).update_stock_price()
        exit("stock price updated done")
    while True:
        try:
            # 策略1启动
            Strategy1.Strategy1(db, traderService).run()
            # 策略2启动
            #Strategy2.Strategy2(db, traderService).run()
            traderService.xt_trader.run_forever()
            #xtdata.run()
        except Exception as e:
            print(f"捕获到异常：{e}")  # 打印异常描述
            # 打印详细的错误位置信息（包括文件名、行号等）
            traceback.print_exc()
        finally:
            # 释放线程池
            db.close()
            if traderService is not None:
                traderService.xt_trader.stop()
