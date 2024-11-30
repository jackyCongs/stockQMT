# coding=utf-8

from xtquant import xtdata
from db.db_pool import DBPool
import db.stock as stock_db
import helper.spider as spider
import helper.data_loader as data_loader


# 全局变量
xtdata.enable_hello = False
db_instance = DBPool()
# 等待被初始化的全局场内基金
inner_stock_infos = {}


def stock_handler(msgs):
    for code in msgs:
        print(msgs[code])
        print(code)


if __name__ == '__main__':
    while True:
        try:
            db_instance.initialize_pool()
            data_loader.load_inner_stock(db_instance, inner_stock_infos)
            for code in inner_stock_infos:
                print('*********')
                print(inner_stock_infos[code])

            SId = xtdata.subscribe_whole_quote(["161125.SZ", "161729.SZ"], callback=stock_handler)
            print(SId)
            xtdata.run()
        except Exception as e:
            print(e)
        finally:
            # 释放线程池
            db_instance.close()
