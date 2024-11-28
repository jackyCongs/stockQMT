# coding=utf-8

from xtquant import xtdata
from db.db_pool import DBPool


def descript(datas):
    for stock_code in datas:
        print(datas[stock_code])


if __name__ == '__main__':
    xtdata.enable_hello = False
    db_instance = DBPool()
    try:
        db_instance.initialize_pool()

        # shanghai_funds = xtdata.get_stock_list_in_sector("沪深基金")
        # print(shanghai_funds)
        res = xtdata.subscribe_whole_quote(["161125.SZ", "161729.SZ"], callback=descript)
        print(res)
        xtdata.run()
    except Exception as e:
        print(e)
    finally:
        # 释放线程池
        db_instance.close()
