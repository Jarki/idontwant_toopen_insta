import datetime
import logging
import threading

import sqlite3

from . import base, models
from .. import constants
from .. import utils


logger = logging.getLogger(__name__)

class SqliteRepository(base.Repository):
    def __init__(self, db_path: str="data/reels.db"):
        self.db_path = db_path
        self.conn = self._get_connection()
        self.conn.row_factory = utils.ig_reel_model_factory
        self.write_lock = threading.Lock()

    def _get_connection(self):
        try:
            return self.conn
        except AttributeError:
            return sqlite3.connect(self.db_path, check_same_thread=False)
        except Exception as e:
            logger.exception(f"Failed to get connection: {e}")
            return None

    def create_database(self):
        sql = """
CREATE TABLE IF NOT EXISTS reels (
    id TEXT PRIMARY KEY,
    title TEXT,
    description TEXT,
    filepath TEXT,
    url TEXT,
    like_count INTEGER,
    created_at DATETIME,
    comments TEXT
);
        """
        conn = self._get_connection()
        conn.execute(sql)
        conn.commit()

    def get_reel_by_id(self, reel_id: str) -> models.IgReel:
        sql = """
SELECT * FROM reels 
WHERE id = ?
AND created_at > ?;
"""
        conn = self._get_connection()
        try:
            cursor = conn.execute(sql, (reel_id, datetime.datetime.now() - constants.REEL_STALE_TIME))
            reel = cursor.fetchone()
        except Exception as e:
            logger.exception(f"Failed to get reel by id: {e}")
            return None
        return reel
    
    def insert_reel(self, reel: models.IgReel) -> None: 
        sql = """
INSERT OR REPLACE INTO reels (id, title, description, filepath, url, like_count, created_at, comments)
VALUES (?, ?, ?, ?, ?, ?, ?, ?);
"""
        conn = self._get_connection()
        with self.write_lock:
            try:
                conn.execute(
                    sql,
                    (reel.id, reel.title, reel.description, reel.filepath, reel.url, reel.like_count, reel.created_at, reel.comments) 
                )
                conn.commit()
                logger.debug(f"Insert reel {reel.id}")
            except Exception as e:
                logger.exception(f"Failed to insert reels: {e}")
                return