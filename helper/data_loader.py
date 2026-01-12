# coding=utf-8

from helper.time_utils import get_datetime
from db import stock as stock_db
from datetime import datetime, timedelta
from decimal import Decimal
from helper import spider, utils, date_utils
import logging
from tqdm import tqdm
from xtquant import xtdata
import json

logger = logging.getLogger(__name__)


def load_inner_stock(db_instance, inner_stock_infos, inner_etf_type):
    stocks = stock_db.get_stock_list(db_instance, inner_etf_type)
    trading_dates = xtdata.get_trading_dates("SZ", date_utils.get_past_date_str(15), get_datetime().strftime("%Y%m%d"))
    is_today_trading = date_utils.is_today_trading()
    if is_today_trading:
        worth_date = date_utils.transfer_date(trading_dates[len(trading_dates) - 2])
    else:
        worth_date = date_utils.transfer_date(trading_dates[len(trading_dates) - 1])
    print(f"上一日交易、净值日期为: {worth_date}, 今天是否是交易日：{is_today_trading}, 请输入 'yes' 继续或 'no' 退出: ")
    while True:
        user_input = input().strip().lower()
        if user_input == 'yes':
            print("用户确认，继续执行...")
            break
        elif user_input == 'no':
            print("用户取消，程序退出")
            exit(0)
        else:
            print("输入无效，请重新输入 'yes' 或 'no'")

    pbar = tqdm(total=len(stocks), desc="inner_stock loading...", mininterval=1)
    for stock in stocks:
        try:
            net_worth = None
            if stock['net_worth']:
                net_worth = json.loads(stock['net_worth'])
            # 如果没有净值数据 或 没有当日最新的数据，则取加载最新的数据并存起来
            if net_worth is None or net_worth['net_worth_date'] != worth_date:
                net_worth = spider.get_last_net_worth(stock['code'])
                if net_worth['code'] != 200:
                    logger.error(f"{stock['code']}, 获取基金净值信息失败: {net_worth['msg']}")
                    continue
                if net_worth['bonus_date'] is not None and net_worth['bonus_date'] == get_datetime().strftime("%Y-%m-%d") \
                        and net_worth['bonus_date'] != net_worth['bonus_date']:
                    logger.info(f"【{stock['code']}】今天有分红，每份除权{net_worth['bonus_money']}元")
                    net_worth['net_worth'] = float(net_worth['net_worth']) - float(net_worth['bonus_money'])
                # 存起来净值
                stock_db.update_stock_net_worth(db_instance, json.dumps(net_worth), net_worth['net_worth_date'], stock['id'])
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
                'hold_status': 0,
                'hold_num': 0,
                'hold_date': "",
                'premium': 0,
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


# 刷新持仓数据
def fresh_holding(inner_stock_infos, holding):
    # 将holding转换为字典以便快速查询，键为股票代码
    holding_dict = {hold.stock_code: hold for hold in holding}
    print(f"start fresh holding {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")

    # 遍历所有股票信息，更新持仓状态和数量
    for stock_code, info in inner_stock_infos.items():
        if stock_code in holding_dict:
            # 当前持有该股票，更新持仓信息
            hold = holding_dict[stock_code]
            info.update({
                # todo 这里有问题，目前hold_num是可卖的数量，计算总持有金额的时候会出现超额购买的问题
                'hold_can_use_num': round(hold.can_use_volume / 100),
                'hold_num': round(hold.volume / 100),
                'hold_status': 1 # [0没持有， 2买入中， 1持有中]
            })
            # print(f"stock_code:{stock_code}, can_use_volume:{round(hold.can_use_volume / 100)}")
        else:
            # 未持有该股票，重置为0
            info.update({
                'hold_num': 0,
                'hold_status': 0,
                'hold_can_use_num': 0
            })
    print(f"done fresh holding {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")


def load_stock(db_instance, stock_codes, stock_infos, holding):
    # 持仓列表
    holding_map = {}
    for hold in holding:
        holding_map[hold.stock_code] = round(hold.can_use_volume / 100)
    hold_num = 0
    pbar = tqdm(total=len(stock_codes), desc="inner_stock loading...", mininterval=1)
    for stock_code in stock_codes:
        pbar.update(1)
        stock = stock_db.get_stock_by_code(db_instance, stock_code)
        if stock is None:
            print(f"加载{stock_code}时，不存在")
            continue
        if utils.enhance_stock_code(stock['code']) in holding_map:
            hold_num = holding_map[utils.enhance_stock_code(stock['code'])]
        stock_infos[utils.enhance_stock_code(stock['code'])] = {
            'code': stock['code'],
            'name': stock['name'],
            'hold_num': hold_num,
            'hold_can_use_num': 0,
            'askPrice': [],
            'askVol': [],
            'askTrendPrice': 0,
            'askTrendVol': [],
            'bidPrice': [],
            'bidVol': [],
            'bidTrendPrice': 0,
            'bidTrendVol': [],
            'hold_status': 0,
            'status': False,
        }
    pbar.close()


def get_all_inner_stocks_code(db_instance, inner_etf_type):
    stocks = stock_db.get_stock_list(db_instance, inner_etf_type)
    codes = []
    for stock in stocks:
        if utils.enhance_stock_code(stock['code']) == stock['code']:
            continue
        codes.append(utils.enhance_stock_code(stock['code']))
    return codes


def convert_enhance_code(codes):
    res = []
    for code in codes:
        res.append(utils.enhance_stock_code(code))
    return res


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


def get_rest_index(target_index_infos):
    # 没有就绪的指数，用另一种方式监听
    rest_index_codes = []
    for stock_code in target_index_infos:
        if target_index_infos[stock_code]['status']:
            continue
        rest_index_codes.append(stock_code)
    return rest_index_codes


def get_previous_date():
    today = get_datetime()
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


def get_premium(increase_rate, base_premium_threshold):
    increase_rate_pct = increase_rate * Decimal(100)
    base = Decimal(base_premium_threshold)
    if increase_rate_pct <= 0:
        return base
    elif increase_rate_pct <= 3:
        return base + increase_rate_pct / Decimal(6)
    else:
        return base + (Decimal(3) / Decimal(6)) + (increase_rate_pct - Decimal(3)) / Decimal(3)


def get_sell_premium(increase_rate):
    increase_rate = increase_rate * Decimal(100)
    if increase_rate >= 0:
        return Decimal(0)
    abs_rate = abs(increase_rate)
    if abs_rate <= 2:
        divisor = Decimal('4.5')
    else:
        divisor = Decimal('5')
    return abs_rate / divisor


def print_top_variance(inner_stock_infos):
    sorted_data = {key: value for key, value in sorted(inner_stock_infos.items(), key=lambda x: x[1]['premium'], reverse=True)}
    i = 0
    for stock_code in sorted_data:
        stock_info = sorted_data[stock_code]
        if i >= 2:
            break
        logger.info(f"top2: {stock_info['name']}-{stock_info['code']}, 折价率{stock_info['premium']}%")
        # 输出信息后归零，防止出现 spinning 的情况
        inner_stock_infos[stock_code].update({'premium': 0})
        i += 1

