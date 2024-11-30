# coding=utf-8
import time

from xtquant import xtdata
from db.db_pool import DBPool
import db.stock as stock_db
import helper.spider as spider
import helper.data_loader as data_loader
from helper import utils


# 全局变量
xtdata.enable_hello = False
db = DBPool()
# 等待被初始化的全局场内基金
inner_stock_infos = {}
# 等待被初始化的全局指数
target_index_infos = {}


def stock_handler(msgs):
    for code in msgs:
        inner_stock_infos[code].update({
            'askPrice': msgs[code]['askPrice'],
            'askVol': msgs[code]['askVol'],
            'bidPrice': msgs[code]['bidPrice'],
            'bidVol': msgs[code]['bidVol'],
        })
        print(inner_stock_infos[code])


def index_handler(msgs):
    for code in msgs:
        print(code)
        print(msgs[code])


if __name__ == '__main__':
    while True:
        try:
            db.initialize_pool()
            data_loader.load_inner_stock(db, inner_stock_infos)
            data_loader.load_target_index(inner_stock_infos, target_index_infos)

            SId1 = xtdata.subscribe_whole_quote(data_loader.get_all_inner_stocks_code(db), callback=stock_handler)
            SId2 = xtdata.subscribe_whole_quote(data_loader.get_all_target_index_code(target_index_infos), callback=index_handler)
            print(f"{SId1}, {SId2}")
            xtdata.run()
        except Exception as e:
            print(e)
        finally:
            # 释放线程池
            db.close()
