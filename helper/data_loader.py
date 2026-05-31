# coding=utf-8
import time

from helper.time_utils import get_datetime
from db import stock as stock_db, index_daily_history
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
                # 存起来净值
                stock_db.update_stock_net_worth(db_instance, json.dumps(net_worth), net_worth['net_worth_date'], stock['id'])

            if net_worth['bonus_date'] is not None and net_worth['bonus_date'] == get_datetime().strftime("%Y-%m-%d"):
                logger.warning(f"【{stock['code']}】今天有分红，每份除权{net_worth['bonus_money']}元")
                net_worth['net_worth'] = float(net_worth['net_worth']) - float(net_worth['bonus_money'])
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
def fresh_holding(inner_stock_infos, target_index_infos, holding):
    # 将holding转换为字典以便快速查询，键为股票代码
    holding_dict = {hold.stock_code: hold for hold in holding}
    print(f"start fresh holding {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
    # 用于临时汇总每个底层指数的总持有市值
    index_exposure_agg = {}
    # 遍历所有股票信息，更新持仓状态和数量
    for stock_code, info in inner_stock_infos.items():
        index_code = info.get('target_index')
        if stock_code in holding_dict:
            hold = holding_dict[stock_code]
            info.update({
                'hold_can_use_num': hold.can_use_volume // 100,
                'hold_num': hold.volume // 100,
                'hold_market_value': float(hold.market_value),
                'hold_status': 1  # [0没持有， 2买入中， 1持有中]
            })
            index_exposure_agg[index_code] = index_exposure_agg.get(index_code, 0.0) + float(hold.market_value)
        else:
            # 未持有该股票，清空相关数据
            info.update({
                'hold_num': 0,
                'hold_can_use_num': 0,
                'hold_market_value': 0.0,
                'hold_status': 0
            })
    for index_code, index_info in target_index_infos.items():
        target_index_infos[index_code]['index_total_market_value'] = index_exposure_agg.get(index_code, 0.0)
    print(f"done fresh holding {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")

def interval_fresh_holding(inner_stock_infos, target_index_infos, trader_service, second=15):
    while True:
        try:
            current_holding = trader_service.get_holding()
            fresh_holding(inner_stock_infos, target_index_infos, current_holding)
            time.sleep(second)
        except Exception as e:
            print(f"interval_fresh_holding 处理错误: {e}")

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


def load_target_index(db, inner_stock_infos, target_index_infos, yesterday_date):
    pbar = tqdm(total=len(inner_stock_infos), desc="index loading...", mininterval=0.1)
    for code in inner_stock_infos:
        if inner_stock_infos[code]['target_index'] not in target_index_infos:
            relation = [code]
        else:
            relation = target_index_infos[inner_stock_infos[code]['target_index']]['relation']
            if code not in relation:
                relation.append(code)
        # get penalty_rate
        penalty_rate = index_daily_history.get_index_penalty_rate(db, inner_stock_infos[code]['target_index'], yesterday_date)
        target_index_infos[inner_stock_infos[code]['target_index']] = {
            'relation': relation,
            'penalty_rate': penalty_rate,
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


def get_overheating_penalty(increase_rate):
    """
    计算开仓所需的最低折价率 (单日过热：非线性平方加速防守模型)
    :param increase_rate: 今日实时涨幅，例如 0.045 表示 4.5%
    :return: 最终要求的折价率门槛 (Decimal类型)
    """
    increase_rate_pct = Decimal(increase_rate) * Decimal('100')
    # 如果没涨或下跌，无过热风险，直接返回基础利润要求
    if increase_rate_pct <= Decimal('0'):
        return Decimal('0')
    # 2. 绝对免罚安全线：死死卡在 1.0% (只要涨幅在1%以内，统统不罚)
    safe_threshold = Decimal('0.5')
    # 3. 算出“违规超涨部分”: Max(0, 实际涨幅 - 1.0%)
    excess_increase = max(Decimal('0'), increase_rate_pct - safe_threshold)
    # 如果没有超涨，直接返回基础值
    if excess_increase == Decimal('0'):
        return Decimal('0')
    # 4. 核心引擎：平方加速惩罚
    # 公式: 惩罚扣分 = (0.15 × 超涨) + (0.025 × 超涨²)
    k1 = Decimal('0.15')
    k2 = Decimal('0.025')
    penalty = (k1 * excess_increase) + (k2 * (excess_increase ** 2))
    return  penalty


def run_granular_tests():
    print("=" * 55)
    print(f"{'今日指数涨幅':<15} | {'要求最低折价率':<18} | {'状态备注'}")
    print("-" * 55)

    # 循环从 -10 到 80，代表 -1.0% 到 8.0%，步长 0.1%
    for i in range(-10, 81):
        # 计算百分比形式 (如 1.5) 和 实际传参形式 (如 0.015)
        rate_pct = i / 10.0
        rate_val = rate_pct / 100.0

        # 格式化传参，防止浮点数无限小数问题影响 Decimal
        rate_str = f"{rate_val:.3f}"

        # 获取折价率要求
        required_discount = get_overheating_penalty(rate_str)

        # 添加动态的状态备注
        marker = ""
        if rate_pct < 0:
            marker = "下跌安全区"
        elif 0 <= rate_pct <= 0.5:
            marker = "平稳安全区 (无惩罚)"
        elif rate_pct == 0.6:
            marker = "<-- [越过红线，惩罚启动]"
        elif rate_pct == 2.5:
            marker = "<-- [中度过热，丝滑过渡]"
        elif rate_pct == 4.5:
            marker = "<-- [严重过热，加速发力]"
        elif rate_pct == 8.0:
            marker = "<-- [极限情绪，绝对防守]"

        print(f"{rate_pct:>8.1f}%       | {required_discount:>10.4f}%          | {marker}")

    print("=" * 55)
    print("颗粒度 0.1% 测试打印完成。")


def get_sell_premium(increase_rate):
    increase_rate_pct = Decimal(increase_rate) * Decimal('100')
    if increase_rate_pct >= Decimal('0'):
        return Decimal('0')
    drop_pct = abs(increase_rate_pct)
    panic_threshold = Decimal('4.0')
    if drop_pct >= panic_threshold:
        return Decimal('0')
    noise_threshold = Decimal('0.3')
    if drop_pct <= noise_threshold:
        return Decimal('0')
    excess_drop = drop_pct - noise_threshold
    # 黄金参数：前期要价极低保证成交率，跌幅3.0%时要价卡在0.58%不超标
    k1 = Decimal('0.12')
    k2 = Decimal('0.035')
    buffer = (k1 * excess_drop) + (k2 * (excess_drop ** 2))
    return buffer


def run_granular_sell_tests():
    print("=" * 65)
    print(f"{'盘中涨跌幅':<12} | {'要求卖出溢价率':<15} | {'系统状态与动作备注'}")
    print("-" * 65)

    # 循环：从涨 1.0% (10) 一路跌到 跌 5.0% (-50)，步长 0.1%
    for i in range(10, -51, -1):
        rate_pct = i / 10.0
        rate_val = rate_pct / 100.0
        # 格式化传参
        rate_str = f"{rate_val:.3f}"
        # 获取溢价率要求
        required_premium = get_sell_premium(rate_str)
        # 动态备注生成
        marker = ""
        if rate_pct > 0:
            marker = "上涨区 [随时平价落袋]"
        elif rate_pct == 0:
            marker = "平盘区 [随时平价落袋]"
        elif -0.3 <= rate_pct < 0:
            if rate_pct == -0.3:
                marker = "噪音极限 [0缓冲市价卖出] <== (滤网边缘)"
            else:
                marker = "盘口噪音 [0缓冲市价卖出]"
        elif -4.0 < rate_pct < -0.3:
            if rate_pct == -0.4:
                marker = "防御启动 [开始索要反弹溢价] <== (抛压确立)"
            elif rate_pct == -2.0:
                marker = "中度洗盘 [防守墙加高，死扛]"
            elif rate_pct == -3.9:
                marker = "防守极限 [极度危险，索要极高溢价] <== (崩盘前夜)"
            else:
                marker = "加速惜售中..."
        elif rate_pct <= -4.0:
            if rate_pct == -4.0:
                marker = "核按钮触发！[放弃防守，0溢价逃命] <== (股灾熔断)"
            else:
                marker = "股灾区 [无条件市价清仓]"
        print(f"{rate_pct:>7.1f}%      | {required_premium:>10.4f}%        | {marker}")
    print("=" * 65)
    print("测试完成。请核对 0.3%的噪音界限 和 4.0%的核按钮界限 是否精准触发。")

#超额波动惩罚
# 周一涨2%，周二跌4%，周三涨1.5%
# fund_past_3_days = [0.02, -0.04, 0.015]
# 确保顺序也是 周一、周二、周三
# shanghai_index = [0.005, -0.01, 0.002]  # 上证指数: 涨0.5%, 跌1%, 涨0.2%
# shenzhen_index = [0.008, -0.015, 0.005] # 深证成指: 涨0.8%, 跌1.5%, 涨0.5%

# Bundle the indices together
# 将三大指数打包成一个二维列表
# indices_past_3_days = [
#     shanghai_index,
#     shenzhen_index,
#     hushen300_index
# ]
# 每天收盘时计算，计算结果存到数据库，以追踪的指数为单位
def calc_daily_excess_volatility_batch(fund_rates, index_rates_list):
    """
    计算超额波动率。
    - 支持 1~3 天的数据（新加入的标的可能不足3天）
    - 当有3天数据时，丢弃波动最小的1天，只取波动最大的2天计算
    - 当只有1~2天数据时，用所有可用天数计算
    """
    num_days = len(fund_rates)
    if num_days == 0:
        return Decimal('0')

    # 计算每天的超额波动
    daily_excess = []
    for i in range(num_days):
        # 取绝对值
        f_vol = abs(Decimal(str(fund_rates[i])) * Decimal('100'))

        # 找出当天三大指数中波动最大的基准值
        # 需要确保基准指数在该天也有数据
        valid_idx_vols = []
        for idx in index_rates_list:
            if i < len(idx):
                valid_idx_vols.append(abs(Decimal(str(idx[i])) * Decimal('100')))

        if valid_idx_vols:
            max_idx_vol = max(valid_idx_vols)
        else:
            max_idx_vol = Decimal('0')

        # 当天的超额波动
        excess_vol = max(Decimal('0'), f_vol - max_idx_vol)
        daily_excess.append(excess_vol)

    if num_days >= 3:
        # 丢弃波动最小的1天，只取波动最大的2天
        daily_excess.sort(reverse=True)
        daily_excess = daily_excess[:2]

    # 返回所选天数的超额波动总和
    return sum(daily_excess)


def get_penalty(db_excess_volatility):
    # 确保入参转换为高精度 Decimal
    excess_vol = Decimal(str(db_excess_volatility))

    # 静态规则与参数 (O(1) 极速计算)
    tolerance = Decimal('1.5')

    # 如果没超过容忍度，直接 0 消耗返回
    if excess_vol <= tolerance:
        return Decimal('0')

    punishable_vol = excess_vol - tolerance

    # 极度克制的惩罚系数
    k1 = Decimal('0.03')
    k2 = Decimal('0.015')

    # 计算纯粹的惩罚值
    pure_penalty = (k1 * punishable_vol) + (k2 * (punishable_vol ** 2))

    return pure_penalty


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

