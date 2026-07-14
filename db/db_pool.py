# coding=utf-8

from dbutils.pooled_db import PooledDB
import pymysql
import threading
import logging
import configparser

# Configure logging
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
                creator=pymysql,  # Use pymysql as database driver
                maxconnections=20,  # Maximum number of connections allowed in the pool
                mincached=2,  # Minimum idle connections created on pool initialization (0 means none)
                maxcached=5,  # Maximum idle connections in the pool (0 or None means no caching)
                maxshared=3,  # Maximum shared connections in the pool (0 or None means all shared)
                blocking=False,  # Whether to block when no connections are available in the pool
                ping=2,
                setsession=[],
                connect_timeout=3,  # Maximum wait time for establishing a new connection (3 seconds)
                write_timeout=3,  # Maximum wait time for sending ping packets (3 seconds, prevents zombie connections)
                read_timeout=3,
                **db_config  # Other database connection parameters
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