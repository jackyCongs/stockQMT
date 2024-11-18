# coding=utf-8
from xtquant import xtdata

if __name__ == '__main__':
    shanghai_funds = xtdata.get_stock_list_in_sector("沪深基金")
    print(shanghai_funds)