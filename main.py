# coding=utf-8

from xtquant import xtdata
from db.db_pool import DBPool


if __name__ == '__main__':
    db_instance = DBPool()
    try:
        db_instance.initialize_pool()

        shanghai_funds = xtdata.get_stock_list_in_sector("沪深基金")
        print(shanghai_funds)
    except Exception as e:
        print(e)
    finally:
        # 释放线程池
        db_instance.close()
