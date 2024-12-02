# coding=utf-8

import configparser
from xtquant.xttype import StockAccount

config = configparser.ConfigParser()
config.read('config.ini')


def get_account():
    return StockAccount(config["ACCOUNT"]["id"])


def get_path():
    return StockAccount(config["ACCOUNT"]["path"])
