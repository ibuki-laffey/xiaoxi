import os
import json
from datetime import datetime, timedelta
import requests

class TimeService:
    """时间服务 - 每次调用都获取最新北京时间。"""
    
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.cache = None
        self.cache_timeout = 30
    
    def _log(self, msg: str, level: str = "INFO"):
        if self.log_callback:
            try: self.log_callback(msg, level=level)
            except TypeError: self.log_callback(msg)
    
    def get_beijing_time(self):
        """获取北京时间（优先网络，fallback 本地）。"""
        if self.cache:
            ts, cached = self.cache
            if (datetime.now().timestamp() - ts) < self.cache_timeout:
                return cached
        
        apis = [
            ("淘宝", "http://api.m.taobao.com/gw/api.md.time.query.json"),
            ("WorldTime", "https://worldtimeapi.org/api/timezone/Asia/Shanghai"),
            ("百度", "https://www.baidu.com")
        ]
        for name, url in apis:
            try:
                resp = requests.get(url, timeout=3, headers={'User-Agent': 'Mozilla/5.0'})
                if resp.status_code != 200: continue
                
                if "taobao" in url:
                    data = resp.json()
                    ts = data.get('data', {}).get('time')
                    if ts:
                        dt = datetime.fromtimestamp(int(ts) / 1000)
                        result = dt.strftime("%Y 年%m 月%d 日 %H 时%M 分%S 秒")
                        self.cache = (datetime.now().timestamp(), result)
                        return result
                elif "worldtimeapi" in url:
                    data = resp.json()
                    dt_str = data.get('datetime')
                    if dt_str:
                        result = dt_str.replace('T', ' ').split('+')[0]
                        self.cache = (datetime.now().timestamp(), result)
                        return result
                elif "baidu" in url:
                    date_hdr = resp.headers.get('Date')
                    if date_hdr:
                        gmt_dt = datetime.strptime(date_hdr, "%a, %d %b %Y %H:%M:%S GMT")
                        beijing_dt = gmt_dt + timedelta(hours=8)
                        result = beijing_dt.strftime("%Y 年%m 月%d 日 %H 时%M 分%S 秒")
                        self.cache = (datetime.now().timestamp(), result)
                        return result
            except Exception:
                continue
        
        local = datetime.now().strftime("%Y 年%m 月%d 日 %H 时%M 分%S 秒")
        self.cache = (datetime.now().timestamp(), local)
        return local
    
    def get_timestamp(self):
        """获取时间戳（秒）。"""
        return datetime.now().timestamp()
