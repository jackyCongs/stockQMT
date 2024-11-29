# coding=utf-8

from xtquant import xtdata
from db.db_pool import DBPool


def descript(datas):
    for stock_code in datas:
        print(datas[stock_code])


if __name__ == '__main__':
    xtdata.enable_hello = False
    db_instance = DBPool()

    while True:
        try:
            db_instance.initialize_pool()

            # res = xtdata.subscribe_whole_quote(["161125.SZ", "161729.SZ"], callback=descript)
            # print(res)
            # xtdata.run()
            count = 0
            funds = xtdata.get_stock_list_in_sector("沪深基金")
            for fund_code in funds:
                single = xtdata.get_instrument_detail(fund_code)
                if 'LOF' in single['InstrumentName']:
                    count += 1
                    # {'ExchangeID': 'SH', 'InstrumentID': '502023', 'InstrumentName': '钢铁LOF', 'ProductID': '', 'ProductName': '', 'ProductType': None, 'ExchangeCode': '502023', 'UniCode': '502023', 'CreateDate': '0', 'OpenDate': '20150824', 'ExpireDate': '99999999', 'PreClose': 1.502, 'SettlementPrice': 1.502, 'UpStopPrice': 1.652, 'DownStopPrice': 1.352, 'FloatVolume': 259331971.0, 'TotalVolume': 259331971.0, 'LongMarginRatio': 0.0, 'ShortMarginRatio': 0.0, 'PriceTick': 0.001, 'VolumeMultiple': 1, 'MainContract': 0, 'LastVolume': 0, 'InstrumentStatus': 0, 'IsTrading': False, 'IsRecent': False, 'ProductTradeQuota': 0, 'ContractTradeQuota': 0, 'ProductOpenInterestQuota': 0, 'ContractOpenInterestQuota': 0}
                    print(single)
            print(f'一共有{count}个LOF基金')
        except Exception as e:
            print(e)
        finally:
            # 释放线程池
            db_instance.close()
