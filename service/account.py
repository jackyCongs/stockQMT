# coding=utf-8

import configparser
from xtquant.xttype import StockAccount

config = configparser.ConfigParser()
config.read('config.ini')


def get_account():
    return StockAccount(config["ACCOUNT"]["id"])


def get_path():
    return "D:\\QMT\\迅投极速策略交易系统交易终端 大同证券QMT实盘\\userdata_mini"
