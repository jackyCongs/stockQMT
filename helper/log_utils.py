from datetime import datetime
import os
import logging
import sys

def init_logging(strategy_id, mode):
    current_file_path = os.path.abspath(__file__)
    helper_dir = os.path.dirname(current_file_path)
    root_dir = os.path.dirname(helper_dir)
    log_dir = os.path.join(root_dir, 'logs')
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    # 2. Get current date
    curr_date = datetime.now().strftime("%Y%m%d")
    # 3. Determine file prefix: use strategy ID for trade mode, else use mode number
    log_prefix = strategy_id if strategy_id else f"mode_{mode}"
    log_filename = f"{log_dir}/{log_prefix}_{curr_date}.log"
    # 4. Configure logging
    log_format = '%(asctime)s | %(levelname)s | %(name)s.%(funcName)s:%(lineno)d | %(message)s'
    # Clear existing handlers to prevent duplicate configurations
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler(log_filename, encoding='utf-8', mode='a'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)