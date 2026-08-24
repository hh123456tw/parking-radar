"""唯讀系統狀態：門檻分級與本機資源讀取，不做任何外部請求。"""

import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from analytics_database import fetch_status_times

APP_STARTED_AT = datetime.now(timezone.utc)
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def classify_data_age(minutes):
    """官方/Collector 資料年齡：<=30 綠、<=60 黃、>60 紅、未知灰。"""
    if minutes is None:
        return {"tone": "gray"}
    if minutes <= 30:
        return {"tone": "green"}
    if minutes <= 60:
        return {"tone": "yellow"}
    return {"tone": "red"}


def classify_metadata_age(minutes):
    """月度後設資料：35 天內綠、45 天內黃、再久紅、未知灰。"""
    if minutes is None:
        return {"tone": "gray"}
    if minutes <= 35 * 24 * 60:
        return {"tone": "green"}
    if minutes <= 45 * 24 * 60:
        return {"tone": "yellow"}
    return {"tone": "red"}


def classify_mysql_latency(milliseconds):
    """MySQL 延遲：<100 綠、<=500 黃、>500 或失敗紅、未知灰。"""
    if milliseconds is None:
        return {"tone": "gray"}
    if milliseconds < 100:
        return {"tone": "green"}
    if milliseconds <= 500:
        return {"tone": "yellow"}
    return {"tone": "red"}


def classify_memory(percent):
    """記憶體使用率：<80 綠、<=90 黃、>90 紅、未知灰。"""
    if percent is None:
        return {"tone": "gray"}
    if percent < 80:
        return {"tone": "green"}
    if percent <= 90:
        return {"tone": "yellow"}
    return {"tone": "red"}


def classify_disk(percent_remaining):
    """磁碟剩餘比例：>20 綠、>=10 黃、<10 紅、未知灰。"""
    if percent_remaining is None:
        return {"tone": "gray"}
    if percent_remaining > 20:
        return {"tone": "green"}
    if percent_remaining >= 10:
        return {"tone": "yellow"}
    return {"tone": "red"}


def classify_load(load_5m):
    """5 分鐘負載：<0.25 綠、<=0.6 黃、>0.6 紅、未知灰。"""
    if load_5m is None:
        return {"tone": "gray"}
    if load_5m < 0.25:
        return {"tone": "green"}
    if load_5m <= 0.6:
        return {"tone": "yellow"}
    return {"tone": "red"}


def app_uptime_minutes(now=None):
    """回傳應用程式啟動至今的整數分鐘數，最少為 0。"""
    current = now or datetime.now(timezone.utc)
    return max(0, int((current - APP_STARTED_AT).total_seconds() // 60))


def read_linux_status(meminfo_path=None, disk_path=None):
    """讀取記憶體、5 分鐘負載與磁碟剩餘；任何失敗都以 None 表示未知。"""
    meminfo = Path(meminfo_path) if meminfo_path is not None \
        else Path("/proc/meminfo")
    disk = disk_path if disk_path is not None else "/"
    return {
        "memory_percent": _read_memory_percent(meminfo),
        "load_5m": _read_load_5m(),
        "disk_remaining_percent": _read_disk_remaining_percent(disk),
    }


def _read_memory_percent(meminfo_path):
    """解析 /proc/meminfo 的 MemTotal/MemAvailable，缺任一值回傳 None。"""
    try:
        values = {}
        for line in meminfo_path.read_text(encoding="utf-8").splitlines():
            if ":" in line:
                name, rest = line.split(":", 1)
                values[name.strip()] = int(rest.split()[0])
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if total is None or available is None:
            return None
        return round((total - available) / total * 100, 1)
    except (OSError, ValueError, IndexError):
        return None


def _read_load_5m():
    """讀取 os.getloadavg 的 5 分鐘值；不支援的平台回傳 None。"""
    try:
        loadavg = getattr(os, "getloadavg", None)
        if loadavg is None:
            return None
        return float(loadavg()[1])
    except (OSError, AttributeError):
        return None


def _read_disk_remaining_percent(disk_path):
    """回傳磁碟剩餘比例；路徑不可用時回傳 None。"""
    try:
        usage = shutil.disk_usage(disk_path)
        return round(usage.free / usage.total * 100, 1)
    except OSError:
        return None


def build_status(connection, now_utc=None, deploy_version="unknown",
                 analytics_enabled=False):
    """組合唯讀系統狀態；每個元件獨立失敗，未知一律灰色。"""
    now_utc = now_utc or datetime.now(timezone.utc)
    try:
        times = fetch_status_times(connection)
    except Exception:
        times = {}
    system = read_linux_status()
    return {
        "application": _application_component(now_utc),
        "database": _database_component(connection),
        "official_data": _data_age_component(
            "official_data_at", "官方資料", times, now_utc),
        "collector": _data_age_component(
            "collector_at", "Collector", times, now_utc),
        "metadata": _metadata_age_component(times, now_utc),
        "load": _load_component(system),
        "memory": _memory_component(system),
        "disk": _disk_component(system),
        "deploy": _deploy_component(deploy_version),
        "analytics": _analytics_component(analytics_enabled),
    }


def component(label, value, tone, detail):
    """建立狀態元件的最小固定形狀。"""
    return {"label": label, "value": value, "tone": tone, "detail": detail}


def _application_component(now_utc):
    """應用程式元件：只依啟動時間計算，不依賴資料庫或外部服務。"""
    minutes = app_uptime_minutes(now_utc)
    return component(
        "應用程式", f"已運行 {minutes} 分鐘", "green",
        f"啟動於 {_taipei_iso(APP_STARTED_AT)}",
    )


def _database_component(connection):
    """MySQL 元件：執行 SELECT 1 計時，失敗獨立標紅。"""
    try:
        latency_ms = _mysql_latency_ms(connection)
    except Exception:
        return component("MySQL", "連線失敗", "red", "無法執行 SELECT 1")
    return component(
        "MySQL", f"{latency_ms:.1f} ms",
        classify_mysql_latency(latency_ms)["tone"], "SELECT 1 耗時",
    )


def _mysql_latency_ms(connection):
    """測量 SELECT 1 的耗時毫秒數。"""
    started = time.perf_counter()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchall()
    return (time.perf_counter() - started) * 1000


def _data_age_component(key, label, times, now_utc):
    """資料年齡元件；時間缺失或讀取失敗時顯示灰色未知。"""
    value = times.get(key)
    minutes = _age_minutes(value, now_utc)
    detail = "最近時間：" + (_taipei_iso(value) if value is not None
                             else "無資料")
    value_text = f"{minutes} 分鐘前" if minutes is not None else "未知"
    return component(
        label, value_text, classify_data_age(minutes)["tone"], detail)


def _metadata_age_component(times, now_utc):
    """後設資料依每月排程顯示天數，避免套用即時資料門檻造成誤報。"""
    value = times.get("metadata_at")
    minutes = _age_minutes(value, now_utc)
    detail = "最近時間：" + (_taipei_iso(value) if value is not None
                             else "無資料") + "；排程：每月更新"
    value_text = f"{minutes // (24 * 60)} 天前" \
        if minutes is not None else "未知"
    return component(
        "後設資料", value_text, classify_metadata_age(minutes)["tone"], detail)


def _age_minutes(value, now_utc):
    """把 MySQL 的 naive UTC 時間換算為距今整數分鐘。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, int((now_utc - value).total_seconds() // 60))


def _load_component(system):
    load = system.get("load_5m")
    value = f"{load:.2f}" if load is not None else "未知"
    return component(
        "5 分鐘負載", value, classify_load(load)["tone"],
        "os.getloadavg 的 5 分鐘平均值",
    )


def _memory_component(system):
    percent = system.get("memory_percent")
    value = f"{percent:.1f}%" if percent is not None else "未知"
    return component(
        "記憶體", value, classify_memory(percent)["tone"],
        "/proc/meminfo 的使用率",
    )


def _disk_component(system):
    percent = system.get("disk_remaining_percent")
    value = f"{percent:.1f}%" if percent is not None else "未知"
    return component(
        "磁碟剩餘", value, classify_disk(percent)["tone"],
        "shutil.disk_usage 的剩餘比例",
    )


def _deploy_component(deploy_version):
    known = bool(deploy_version) and deploy_version != "unknown"
    return component(
        "部署版本", deploy_version or "未知",
        "green" if known else "gray",
        "DEPLOY_VERSION 注入的 Git commit",
    )


def _analytics_component(enabled):
    if enabled:
        return component(
            "匿名分析", "已啟用", "green", "僅統計同意匿名分析的使用者")
    return component(
        "匿名分析", "未設定", "gray", "缺少 ANALYTICS_HMAC_SECRET 或已停用")


def _taipei_iso(value):
    """把 MySQL 的 naive UTC datetime 轉成台北 ISO 字串。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TAIPEI_TZ).isoformat()
