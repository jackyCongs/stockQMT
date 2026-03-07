# coding=utf-8

from dbutils.pooled_db import PooledDB
import pymysql
import threading
import logging
import configparser

# 配置日志
logger = logging.getLogger(__name__)


class DBPool:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is not None:
            return cls._instance
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DBPool, cls).__new__(cls)
                cls._instance._pool = None
        return cls._instance

    def initialize_pool(self):
        db_config = self.__db_config()
        if self._pool is None:
            self._pool = PooledDB(
                creator=pymysql,  # 使用pymysql作为数据库驱动
                maxconnections=20,  # 连接池允许的最大连接数
                mincached=2,  # 初始化时，连接池中至少创建的空闲的连接，0表示不创建
                maxcached=5,  # 连接池中最多闲置的连接，0和None不缓存
                maxshared=3,  # 连接池中最多共享的连接数量，0和None表示全部共享
                blocking=False,  # 连接池中如果没有可用连接后，是否阻塞等待
                ping=2,
                setsession=[],
                connect_timeout=3,  # 建立新连接最多等 3 秒
                write_timeout=3,  # 发送 ping 包最多等 3 秒（解决僵尸连接卡死的主力）
                read_timeout=3,
                **db_config  # 其他数据库连接参数
            )
            logger.info("MySQL connection pool initialized.")

    def __db_config(self):
        config = configparser.ConfigParser()
        config.read('config.ini', encoding='utf-8')

        db_config = {
            "host": config["DATABASE"]["host"],
            "user": config["DATABASE"]["user"],
            "password": config["DATABASE"]["password"],
            "database": config["DATABASE"]["database"],
            "charset": "utf8"
        }
        return db_config

    def get_connection(self):
        # if self._pool is None:
        #     raise Exception("Connection pool has not been initialized.")
        # try:
        #     return self._pool.connection()
        # except Exception as e:
        #     logger.error(f"Failed to get connection from pool: {e}")
        #     raise
        db_config = self.__db_config()
        conn = pymysql.connect(**db_config)
        return conn

    def close_all(self):
        if self._pool is not None:
            self._pool.closeall()
            logger.info("All connections in the pool have been closed.")

    def close(self):
        if self._pool is not None:
            self._pool.close()
            logger.info("Connection pool has been closed.")