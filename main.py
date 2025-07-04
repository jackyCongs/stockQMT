# coding=utf-8

from xtquant import xtdata
from db.db_pool import DBPool
from service import Trader_service, Trans_flows
import logging
import time
from strategies import  Strategy1, Strategy2
import sys

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
session_id = round(time.time())


if __name__ == '__main__':
    traderService = Trader_service.Trader_service(session_id)
    if len(sys.argv) > 0 and sys.argv[0] == 1:
        Trans_flows.Trans_flows(traderService.get_asset()).run()
        exit("accounting done")
    while True:
        try:
            db.initialize_pool()
            # 策略1启动
            Strategy1.Strategy1(db, traderService).run()
            # 策略2启动
            #Strategy2.Strategy2(db, traderService).run()
            traderService.xt_trader.run_forever()
            #xtdata.run()
        except Exception as e:
            e.with_traceback()
            logging.error(e)
        finally:
            # 释放线程池
            db.close()
            if traderService is not None:
                traderService.xt_trader.stop()
