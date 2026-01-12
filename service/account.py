# coding=utf-8

import configparser
from xtquant.xttype import StockAccount

config = configparser.ConfigParser()
config.read('config.ini')


def get_account(platform):
    if platform == "大同":
        return StockAccount(config["ACCOUNT"]["id"])
    if platform == "湘财":
        return StockAccount(config["ACCOUNT"]["xiangcai_id"])
    exit(f"get account error: {platform}")



def get_path(platform):
    if platform == "大同":
        return "D:\\QMT\\迅投极速策略交易系统交易终端 大同证券QMT实盘\\userdata_mini"
    if platform == "湘财":
        return "D:\\QMT\\湘财迅投QMT极速策略交易系统（交易端）\\userdata_mini"
    exit(f"get path error: {platform}")
