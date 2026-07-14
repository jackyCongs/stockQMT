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
    print(f"Previous trading & net worth date: {worth_date}. Is today a trading day? {is_today_trading}. Enter 'yes' to continue or 'no' to exit: ")
    while True:
        user_input = input().strip().lower()
        if user_input == 'yes':
            print("User confirmed. Continuing execution...")
            break
        elif user_input == 'no':
            print("User cancelled. Exiting program...")
            exit(0)
        else:
            print("Invalid input. Please enter 'yes' or 'no':")

    pbar = tqdm(total=len(stocks), desc="inner_stock loading...", mininterval=1)
    for stock in stocks:
        try:
            net_worth = None
            if stock['net_worth']:
                net_worth = json.loads(stock['net_worth'])
            # If net worth data is missing or not updated for today, fetch the latest and store it
            if net_worth is None or net_worth['net_worth_date'] != worth_date:
                net_worth = spider.get_last_net_worth(stock['code'])
                if net_worth['code'] != 200:
                    logger.error(f"{stock['code']}, Failed to fetch fund net worth details: {net_worth['msg']}")
                    continue
                # Cache/store the net worth
                stock_db.update_stock_net_worth(db_instance, json.dumps(net_worth), net_worth['net_worth_date'], stock['id'])

            if net_worth['bonus_date'] is not None and net_worth['bonus_date'] == get_datetime().strftime("%Y-%m-%d"):
                logger.warning(f"[{stock['code']}] Dividend distribution today: ex-dividend is {net_worth['bonus_money']} CNY per share.")
                if inner_etf_type == 'lof':
                    net_worth['net_worth'] = float(net_worth['net_worth']) - float(net_worth['bonus_money'])
            # If the enhanced stock code remains unchanged, it is invalid; skip it
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
    # Close progress bar upon completion
    pbar.close()


# Refresh holding records
def fresh_holding(inner_stock_infos, target_index_infos, holding):
    # Convert holdings to a dictionary for O(1) lookups using stock code as key
    holding_dict = {hold.stock_code: hold for hold in holding}
    print(f"start fresh holding {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
    # Used to aggregate total market exposure for each underlying index
    index_exposure_agg = {}
    # Iterate through stock data to update holding statuses and volumes
    for stock_code, info in inner_stock_infos.items():
        index_code = info.get('target_index')
        if stock_code in holding_dict:
            hold = holding_dict[stock_code]
            info.update({
                'hold_can_use_num': hold.can_use_volume // 100,
                'hold_num': hold.volume // 100,
                'hold_market_value': float(hold.market_value),
                'hold_status': 1  # [0: not held, 1: held, 2: order pending]
            })
            index_exposure_agg[index_code] = index_exposure_agg.get(index_code, 0.0) + float(hold.market_value)
        else:
            # Clear parameters if stock is not held
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
            print(f"Error in interval_fresh_holding: {e}")


def load_stock(db_instance, stock_codes, stock_infos, holding):
    # Holdings map
    holding_map = {}
    for hold in holding:
        holding_map[hold.stock_code] = round(hold.can_use_volume / 100)
    hold_num = 0
    pbar = tqdm(total=len(stock_codes), desc="inner_stock loading...", mininterval=1)
    for stock_code in stock_codes:
        pbar.update(1)
        stock = stock_db.get_stock_by_code(db_instance, stock_code)
        if stock is None:
            print(f"Failed to load {stock_code}: Ticker does not exist")
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
    # Deduplicate all target_index values and map type as 'index'
    unique_target_indices = set(
        info['target_index'] for info in inner_stock_infos.values() if info.get('target_index')
    )
    code_type_list = [(idx_code, 'index') for idx_code in unique_target_indices]
    
    # Batch query all penalty rates
    penalty_rates = index_daily_history.get_batch_index_penalty_rates(db, code_type_list, yesterday_date)

    pbar = tqdm(total=len(inner_stock_infos), desc="index loading...", mininterval=0.1)
    for code, info in inner_stock_infos.items():
        target_idx = info['target_index']
        if not target_idx:
            logger.warning(f"load_target_index: target index [{target_idx}] does not exist")
            pbar.update(1)
            continue
            
        if target_idx not in target_index_infos:
            relation = [code]
        else:
            relation = target_index_infos[target_idx]['relation']
            if code not in relation:
                relation.append(code)
                
        # get penalty_rate
        penalty_rate = penalty_rates.get(target_idx, 0.0)
        target_index_infos[target_idx] = {
            'relation': relation,
            'penalty_rate': penalty_rate,
            'status': False,
        }
        pbar.update(1)
    pbar.close()


def load_target_index_for_etf(db, inner_stock_infos, target_index_infos, yesterday_date, strategy_etf_type):
    # Batch collect all (code, type) pairs to run a single query
    code_type_list = []
    for code, stock_info in inner_stock_infos.items():
        purified = utils.purified_code(code)
        if not stock_info['target_index'] or not stock_info['target_index'].isdigit():
            # If no tracking index exists, use the ETF's own penalty rate
            code_type_list.append((purified, strategy_etf_type))
        else:
            # If the ETF has an associated tracking index, use the index's penalty rate
            target_idx = utils.purified_code(stock_info['target_index'])
            code_type_list.append((target_idx, 'index'))

    # Retrieve all penalty rates via a single SQL query
    penalty_rates = index_daily_history.get_batch_index_penalty_rates(db, code_type_list, yesterday_date)
    
    for code, stock_info in inner_stock_infos.items():
        index_code = stock_info.get('target_index', '')
        purified = utils.purified_code(code)
        if not stock_info['target_index'] or not stock_info['target_index'].isdigit():
            # If no index exists, treat the ETF code itself as the index group
            index_code = purified
            penalty_rate = penalty_rates.get(purified, 0.0)
        else:
            target_idx = stock_info['target_index']
            penalty_rate = penalty_rates.get(target_idx, 0.0)
            
        target_index_infos[index_code] = {
            #'relation': [code],
            'penalty_rate': penalty_rate,
            'status': True,
            'index_total_market_value': 0.0,
            #'increase_rate': 0
        }


def get_group_code(target_index, etf_code):
    if not target_index or not target_index.isdigit():
        return utils.purified_code(etf_code)
    return target_index


def get_rest_index(target_index_infos):
    # If indices are not ready, use a fallback channel to listen
    rest_index_codes = []
    for stock_code in target_index_infos:
        if target_index_infos[stock_code]['status']:
            continue
        rest_index_codes.append(stock_code)
    return rest_index_codes


def get_previous_date():
    today = get_datetime()
    end_time = today.strftime('%Y%m%d')
    # Calculate date 15 days ago
    fifteen_days_ago = today - timedelta(days=15)
    start_time = fifteen_days_ago.strftime('%Y%m%d')
    dates = xtdata.get_trading_dates("SH", start_time, end_time)

    # If the last date in the list is today, and today is a trading day
    if datetime.fromtimestamp(dates[len(dates) - 1] / 1000).strftime('%Y%m%d') == end_time:
        return datetime.fromtimestamp(dates[len(dates) - 2] / 1000).strftime('%Y-%m-%d')
    # If today is not a trading day, return the last trading date in the database
    return datetime.fromtimestamp(dates[len(dates) - 1] / 1000).strftime('%Y-%m-%d')


def get_overheating_penalty(increase_rate):
    """
    Calculate minimum discount rate required for opening a position (Intraday Overheating: Non-linear Quadratic Acceleration Defense Model).
    :param increase_rate: Intraday real-time increase (e.g., 0.045 represents 4.5%)
    :return: Required minimum discount threshold (Decimal type)
    """
    increase_rate_pct = Decimal(increase_rate) * Decimal('100')
    # If price is down or flat, there is no overheating risk; return baseline requirement (0)
    if increase_rate_pct <= Decimal('0'):
        return Decimal('0')
    # 2. Absolute penalty-free safety threshold: set at 0.5% (no penalty if increase is within 0.5%)
    safe_threshold = Decimal('0.5')
    # 3. Calculate "excessive increase": Max(0, actual increase - safe threshold)
    excess_increase = max(Decimal('0'), increase_rate_pct - safe_threshold)
    # If no excessive increase, return baseline (0)
    if excess_increase == Decimal('0'):
        return Decimal('0')
    # 4. Core engine: Quadratic penalty acceleration
    # Formula: Penalty = (0.15 * excess) + (0.025 * excess^2)
    k1 = Decimal('0.15')
    k2 = Decimal('0.025')
    penalty = (k1 * excess_increase) + (k2 * (excess_increase ** 2))
    return penalty


def run_granular_tests():
    print("=" * 55)
    print(f"{'Index Increase':<15} | {'Min Discount Required':<22} | {'Status Remarks'}")
    print("-" * 55)

    # Loop from -10 to 80, representing -1.0% to 8.0% with a step size of 0.1%
    for i in range(-10, 81):
        # Calculate percentage form (e.g., 1.5) and parameter value (e.g., 0.015)
        rate_pct = i / 10.0
        rate_val = rate_pct / 100.0

        # Format argument to prevent float precision issues from affecting Decimal conversions
        rate_str = f"{rate_val:.3f}"

        # Retrieve required discount rate
        required_discount = get_overheating_penalty(rate_str)

        # Add dynamic status remark
        marker = ""
        if rate_pct < 0:
            marker = "Downside Safety Zone"
        elif 0 <= rate_pct <= 0.5:
            marker = "Flat Safety Zone (No Penalty)"
        elif rate_pct == 0.6:
            marker = "<-- [Breached Threshold, Penalty Active]"
        elif rate_pct == 2.5:
            marker = "<-- [Moderate Overheating, Transitioning]"
        elif rate_pct == 4.5:
            marker = "<-- [Severe Overheating, Accelerating]"
        elif rate_pct == 8.0:
            marker = "<-- [Extreme Emotion, Full Defense]"

        print(f"{rate_pct:>8.1f}%       | {required_discount:>10.4f}%          | {marker}")

    print("=" * 55)
    print("0.1% granularity test printing complete.")


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
    # Golden Parameter: keeps baseline spreads narrow to secure executions; limits premium to 0.58% at a 3.0% drop
    k1 = Decimal('0.12')
    k2 = Decimal('0.035')
    buffer = (k1 * excess_drop) + (k2 * (excess_drop ** 2))
    return buffer


def run_granular_sell_tests():
    print("=" * 65)
    print(f"{'Intraday Chg':<12} | {'Required Premium':<16} | {'System Status & Action Remarks'}")
    print("-" * 65)

    # Loop: from +1.0% (10) down to -5.0% (-50) with a step of 0.1%
    for i in range(10, -51, -1):
        rate_pct = i / 10.0
        rate_val = rate_pct / 100.0
        # Format parameters
        rate_str = f"{rate_val:.3f}"
        # Retrieve required premium rate
        required_premium = get_sell_premium(rate_str)
        # Generate dynamic status remark
        marker = ""
        if rate_pct > 0:
            marker = "Uptrend Zone [Exit at parity anytime]"
        elif rate_pct == 0:
            marker = "Flat Zone [Exit at parity anytime]"
        elif -0.3 <= rate_pct < 0:
            if rate_pct == -0.3:
                marker = "Noise Limit [0-buffer market sell] <== (Filter Edge)"
            else:
                marker = "Micro Noise [0-buffer market sell]"
        elif -4.0 < rate_pct < -0.3:
            if rate_pct == -0.4:
                marker = "Defense Active [Demanding rebound premium] <== (Selling Pressure Established)"
            elif rate_pct == -2.0:
                marker = "Moderate Shakeout [Increase defensive spreads, hold steady]"
            elif rate_pct == -3.9:
                marker = "Defense Limit [Highly dangerous, demanding high premium] <== (Pre-collapse Edge)"
            else:
                marker = "Accelerated supply containment active..."
        elif rate_pct <= -4.0:
            if rate_pct == -4.0:
                marker = "Panic Triggered! [De-escalate defense, escape at 0-premium] <== (Market Crash circuit-breaker)"
            else:
                marker = "Market Collapse Zone [Unconditional market clearance]"
        print(f"{rate_pct:>7.1f}%      | {required_premium:>10.4f}%        | {marker}")
    print("=" * 65)
    print("Test complete. Please verify if the 0.3% noise limit and 4.0% panic trigger fired accurately.")


# Excess volatility penalty
# Monday +2%, Tuesday -4%, Wednesday +1.5%
# Ensure sequence follows Monday, Tuesday, Wednesday
# shanghai_index = [0.005, -0.01, 0.002]  # SSE: +0.5%, -1.0%, +0.2%
# shenzhen_index = [0.008, -0.015, 0.005] # SZSE: +0.8%, -1.5%, +0.5%
# Pack three major indices into a 2D list
# Calculated daily at market close. Results saved to DB, grouped by tracked index code.
def calc_daily_excess_volatility_batch(fund_rates, index_rates_list):
    """
    Calculate excess volatility.
    - Supports 1 to 3 days of historical data (new listings may have < 3 days)
    - If 3 days are available, drop the lowest volatility day and calculate based on the top 2 highest volatility days
    - If only 1 to 2 days are available, calculate based on all available days
    """
    num_days = len(fund_rates)
    if num_days == 0:
        return Decimal('0')

    # Calculate excess volatility for each day
    daily_excess = []
    for i in range(num_days):
        # Take absolute value
        f_vol = abs(Decimal(str(fund_rates[i])) * Decimal('100'))

        # Identify the maximum volatility benchmark among the three major indices for that day
        # Ensure the benchmark index has valid data for that day
        valid_idx_vols = []
        for idx in index_rates_list:
            if i < len(idx):
                valid_idx_vols.append(abs(Decimal(str(idx[i])) * Decimal('100')))

        if valid_idx_vols:
            max_idx_vol = max(valid_idx_vols)
        else:
            max_idx_vol = Decimal('0')

        # Daily excess volatility
        excess_vol = max(Decimal('0'), f_vol - max_idx_vol)
        daily_excess.append(excess_vol)

    if num_days >= 3:
        # Drop the lowest volatility day, keep the top 2 highest volatility days
        daily_excess.sort(reverse=True)
        daily_excess = daily_excess[:2]

    # Return total sum of excess volatility for selected days
    return sum(daily_excess)


def get_penalty(db_excess_volatility):
    # Ensure parameter is converted to high-precision Decimal
    excess_vol = Decimal(str(db_excess_volatility))

    # Static rules and parameters (O(1) calculation complexity)
    tolerance = Decimal('0')

    # If volatility is within tolerance, return 0 immediately
    if excess_vol <= tolerance:
        return Decimal('0')

    punishable_vol = excess_vol - tolerance

    # Conservatively tuned penalty coefficients
    k1 = Decimal('0.03')
    k2 = Decimal('0.015')

    # Calculate raw penalty rate
    pure_penalty = (k1 * punishable_vol) + (k2 * (punishable_vol ** 2))

    return pure_penalty


def print_top_variance(inner_stock_infos):
    sorted_data = {key: value for key, value in sorted(inner_stock_infos.items(), key=lambda x: x[1]['premium'], reverse=True)}
    i = 0
    for stock_code in sorted_data:
        stock_info = sorted_data[stock_code]
        if i >= 2:
            break
        logger.info(f"top2: {stock_info['name']}-{stock_info['code']}, discount rate: {stock_info['premium']}%")
        # Reset to 0 after logging to prevent spinning/infinite loops
        inner_stock_infos[stock_code].update({'premium': 0})
        i += 1
