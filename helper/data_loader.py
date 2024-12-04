# coding=utf-8
import time

from db import stock as stock_db
from datetime import datetime, timedelta
from decimal import Decimal, getcontext
from helper import spider, utils
import logging
from tqdm import tqdm
from xtquant import xtdata

logging.basicConfig(level=logging.INFO,
                    format='%(message)s',
                    filename='logs/app.log',
                    filemode='a')
logger = logging.getLogger(__name__)


# 数据结构:
# 【本方法中初始化】code、name、last_net_worth、last_net_worth_date、withdraw_commission_7rate、处理分红除权、target_index
# 【在监听场内基金里初始化】买卖量价、持有数量、持有天数
# 【在指数监听中初始化】 target_start, target_increase_rate,

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
                logger.error(f"{stock['code']}, 获取基金净值信息失败: {net_worth['msg']}")
                continue
            if net_worth['bonus_date'] is not None and net_worth['bonus_date'] == datetime.now().strftime("%Y-%m-%d") \
                    and net_worth['bonus_date'] != net_worth['bonus_date']:
                logger.info(f"【{stock['code']}】今天有分红，每份除权{net_worth['bonus_money']}元")
                net_worth['net_worth'] = Decimal(net_worth['net_worth']) - Decimal(net_worth['bonus_money'])

            # 如果增强前后值一样，说明是有问题的，直接省略掉
            if utils.enhance_stock_code(stock['code']) == stock['code']:
                continue
            inner_stock_infos[utils.enhance_stock_code(stock['code'])] = {
                'code': stock['code'],
                'name': stock['name'],
                'last_net_worth': Decimal(net_worth['net_worth']),
                'last_net_worth_date': net_worth['net_worth_date'],
                'withdraw_commission_7rate': Decimal(stock['withdraw_commission_7rate'] / 100),
                'target_index': stock['target_worth_url'],
                'hold_status': 0,# [0没用持有， 2买入中， 1持有中]
                'hold_num': 0, #@todo 待添加
                'hold_date': '', #@todo 待添加
                'askPrice': [],
                'askVol': [],
                'bidPrice': [],
                'bidVol': [],
                'status': False,
            }
            pbar.update(1)
        except Exception as e:
            pbar.update(1)
            logger.error(e)
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


def get_all_target_index_code(inner_stock_infos):
    return list(dict.fromkeys(
        [utils.enhance_stock_code(inner_stock_infos[code]['target_index'], 'index')
         for code in inner_stock_infos if
         utils.enhance_stock_code(inner_stock_infos[code]['target_index'], 'index') != code]
    ))


def load_target_index(inner_stock_infos, target_index_infos):
    pbar = tqdm(total=len(inner_stock_infos), desc="index loading...", mininterval=0.1)
    relation = []
    for code in inner_stock_infos:
        relation = []
        if inner_stock_infos[code]['target_index'] not in target_index_infos:
            relation = [code]
        else:
            relation = target_index_infos[inner_stock_infos[code]['target_index']]['relation']
            if code not in relation:
                relation.append(code)
        target_index_infos[inner_stock_infos[code]['target_index']] = {
            'relation': relation,
            'status': False,
        }
        pbar.update(1)
    pbar.close()


def get_previous_date():
    today = datetime.now()
    end_time = today.strftime('%Y%m%d')
    # 计算15天前的日期
    fifteen_days_ago = today - timedelta(days=15)
    start_time = fifteen_days_ago.strftime('%Y%m%d')
    dates = xtdata.get_trading_dates("SH", start_time, end_time)

    # 最后一天是今日，如果今天是交易日
    if datetime.fromtimestamp(dates[len(dates) - 1] / 1000).strftime('%Y%m%d') == end_time:
        return datetime.fromtimestamp(dates[len(dates) - 2] / 1000).strftime('%Y-%m-%d')
    # 最后一天不是交易日，直接输出最后一个交易日
    return datetime.fromtimestamp(dates[len(dates) - 1] / 1000).strftime('%Y-%m-%d')


# 获取策略买入时的动态溢价
def get_premium(increase_rate):
    increase_rate = increase_rate * Decimal(100)
    if increase_rate <= 0:
        return Decimal(0.6)
    elif increase_rate <= 2:
        return Decimal(0.6) + Decimal(increase_rate / Decimal(4))
    return Decimal(increase_rate / Decimal(1.5))