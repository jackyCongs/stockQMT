# coding=utf-8

from db.db_pool import DBPool
import requests
import time
import threading
import json
#from xtquant import xtdatacenter as xtdc


def listen_code(code):
    url = f"https://1.push2.eastmoney.com/api/qt/stock/sse?fields=f58,f734,f107,f57,f43,f59,f169,f170,f152,f46,f60,f44,f45,f47,f48,f19,f17,f531,f15,f13,f11,f20,f18,f16,f14,f12,f39,f37,f35,f33,f31,f40,f38,f36,f34,f32,f211,f212,f213,f214,f215,f210,f209,f208,f207,f206,f161,f49,f171,f50,f86,f168,f108,f167,f71,f292,f51,f52,f191,f192,f452,f177&secid=0.{code}"
    response = requests.get(url, headers={}, timeout=None, stream=True)
    response.raise_for_status()
    last_time = time.time()
    for line in response.iter_lines():
        if line:
            decoded_line = line.decode('utf-8')
            data = json.loads(decoded_line.replace('data: ', ''))
            print(data)
            if data['data'] == None:
                continue
            #print(f"{time.time() - last_time},{line}")
            last_time = time.time()
            if code not in index_pool:
                index_pool[code] = {'time': time.time(), 'val': data['data']['f49'], 'init_val': data['data']['f50']}
            else:
                index_pool.get(code).update({'time': time.time(), 'val': data['data']['f49']})


index_pool = {}


if __name__ == '__main__':
    db_instance = DBPool()
    # 维护一个实时指数池
    listen_index_codes = ['399001', '399006']
    # if '000001' not in index_pool:
    #     index_pool['000001'] = {'time': time.time(), 'val': 329055, 'init_val': 330978}
    # print(index_pool)
    try:
        threads = []
        for code in listen_index_codes:
            thread = threading.Thread(target=listen_code, args=(code,))
            threads.append(thread)
            thread.start()
        # db_instance.initialize_pool()
        while True:
            print(index_pool)
            time.sleep(1)

    except Exception as e:
        print(e)
    finally:
        # 释放线程池
        db_instance.close()
