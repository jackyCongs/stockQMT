# coding=utf-8

from db.db_pool import DBPool
import requests
from sseclient import SSEClient
import time
from xtquant import xtdatacenter as xtdc



if __name__ == '__main__':
    db_instance = DBPool()
    try:
        db_instance.initialize_pool()
        # url = f"https://1.push2.eastmoney.com/api/qt/stock/sse?fields=f58,f734,f107,f57,f43,f59,f169,f170,f152,f46,f60,f44,f45,f47,f48,f19,f17,f531,f15,f13,f11,f20,f18,f16,f14,f12,f39,f37,f35,f33,f31,f40,f38,f36,f34,f32,f211,f212,f213,f214,f215,f210,f209,f208,f207,f206,f161,f49,f171,f50,f86,f168,f108,f167,f71,f292,f51,f52,f191,f192,f452,f177&secid=0.161729"
        # response = requests.get(url, headers={}, timeout=None, stream=True)
        # response.raise_for_status()
        # last_time = time.time()
        # for line in response.iter_lines():
        #     if line:
        #         print(f"{time.time()-last_time},{line}")
        #         last_time = time.time()

    except Exception as e:
        print(e)
    finally:
        # 释放线程池
        db_instance.close()
