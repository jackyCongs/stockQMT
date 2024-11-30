# coding=utf-8

from db import stock as stock_db
from datetime import datetime
from decimal import Decimal, getcontext
from helper import spider, utils
import logging
from tqdm import tqdm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# 数据结构:
# 【本方法中初始化】code、name、last_net_worth、last_net_worth_date、withdraw_commission_7rate、处理分红除权、target_index
# 【在监听场内基金里初始化】买卖量价、持有数量、持有天数
# 【在指数监听中初始化】 target_start, target_increase_rate, target_status【F未就绪，T已就绪】

# 【以下是建议操作的策略】
# 可买的价格 = (target_worth - 分红除权) * (1 + 目标指数的加权涨跌幅) * (1-withdraw_commission_7rate) * (溢价1-0.5) > 比卖价
# 可卖的价格 = (target_worth - 分红除权) * (1 + 目标指数的涨跌幅) * (1-withdraw_commission_7rate) <= 买价
def load_inner_stock(db_instance, inner_stock_infos):
    stocks = stock_db.get_stock_list(db_instance)

    pbar = tqdm(total=len(stocks), desc="inner_stock loading...", mininterval=0.1)
    for stock in stocks:
        try:
            net_worth = spider.get_last_net_worth(stock['code'])
            if net_worth['code'] != 200:
                print(f"{stock['code']}, 获取基金净值信息失败: {net_worth['msg']}")
                continue
            if net_worth['bonus_date'] is not None and net_worth['bonus_date'] == datetime.now().strftime("%Y-%m-%d") \
                    and net_worth['bonus_date'] != net_worth['bonus_date']:
                print(f"【{stock['code']}】今天有分红，每份除权{net_worth['bonus_money']}元")
                net_worth['net_worth'] = Decimal(net_worth['net_worth']) - Decimal(net_worth['bonus_money'])

            # 如果增强前后值一样，说明是有问题的，直接省略掉
            if utils.enhance_stock_code(stock['code']) == stock['code']:
                continue
            inner_stock_infos[utils.enhance_stock_code(stock['code'])] = {
                'code': stock['code'],
                'name': stock['name'],
                'last_net_worth': Decimal(net_worth['net_worth']),
                'last_net_worth_date': net_worth['net_worth_date'],
                'withdraw_commission_7rate': stock['withdraw_commission_7rate'],
                'target_index': stock['target_worth_url'],
                'target_status': False,
                'askPrice': [],
                'askVol': [],
                'bidPrice': [],
                'bidVol': [],
            }
            pbar.update(1)
        except Exception as e:
            pbar.update(1)
            print(e)
    # 完成后关闭进度条
    pbar.close()


def get_all_inner_stocks_code(db_instance):
    stocks = stock_db.get_stock_list(db_instance)
    codes = []
    for stock in stocks:
        if utils.enhance_stock_code(stock['code']) == stock['code']:
            continue
        codes.append(utils.enhance_stock_code(stock['code']))
    return codes


def get_all_target_index_code(target_index_infos):
    codes = []
    for code in target_index_infos:
        if utils.enhance_stock_code(code, 'index') == code:
            continue
        codes.append(utils.enhance_stock_code(code, 'index'))
    return codes

def load_target_index(inner_stock_infos, target_index_infos):
    for code in inner_stock_infos:
        target_index_infos[code] = {}

