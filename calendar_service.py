"""以本地下載的臺灣公開行事曆分類抵達日，並提供明確的下載命令。"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

CALENDAR_DIR = Path("data/calendar")
CALENDAR_BASE_URL = "https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data"
TAIPEI = ZoneInfo("Asia/Taipei")


def _day(kind, label, is_holiday, source):
    """組出統一格式的抵達日分類結果字典。"""
    return {
        "kind": kind,
        "label": label,
        "is_holiday": is_holiday,
        "source": source,
    }


def _load_rows(calendar_file):
    """讀取行事曆 JSON；只保留具備日期與假日旗標的有效資料列。"""
    if not calendar_file.exists():
        return None
    try:
        rows = json.loads(calendar_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    return [
        row for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("date"), str)
        and isinstance(row.get("isHoliday"), bool)
    ]


def classify_arrival_day(arrival_time, calendar_dir=CALENDAR_DIR):
    """依臺灣行事曆把抵達時間分類成平日、週末、國定假日或補班日。"""
    if arrival_time.tzinfo is None:
        raise ValueError("抵達時間必須包含時區")
    local = arrival_time.astimezone(TAIPEI)
    rows = _load_rows(Path(calendar_dir) / f"{local.strftime('%Y')}.json")
    if not rows:
        return _fallback(local)

    by_date = {row["date"]: row for row in rows}
    row = by_date.get(local.strftime("%Y%m%d"))

    if row is None:
        # 檔案存在但缺少抵達日資料列：六日比照 fallback 判為週末，平日維持平日。
        if local.weekday() in (5, 6):
            return _fallback(local)
        return _day("weekday", "平日", False, "taiwan_calendar")

    if row["isHoliday"] and row.get("description"):
        return _day("holiday", f"國定假日｜{row['description']}", True, "taiwan_calendar")
    if row["isHoliday"]:
        return _day("weekend", "週末", True, "taiwan_calendar")
    if local.weekday() == 5:
        return _day("makeup_workday", "補班日", False, "taiwan_calendar")
    return _day("weekday", "平日", False, "taiwan_calendar")


def _fallback(local):
    """行事曆資料不可用時，只依六日粗略分類並標記 weekday_fallback。"""
    if local.weekday() in (5, 6):
        return _day("weekend", "週末", False, "weekday_fallback")
    return _day("weekday", "平日", False, "weekday_fallback")


def sync_calendars(years=None, calendar_dir=CALENDAR_DIR, timeout=10):
    """下載指定年度臺灣行事曆 JSON，並以暫存檔原子換檔後回傳路徑。"""
    if years is None:
        now = datetime.now(TAIPEI)
        years = [now.year, now.year + 1]
    calendar_dir = Path(calendar_dir)
    calendar_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for year in years:
        url = f"{CALENDAR_BASE_URL}/{year}.json"
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError(f"{year}.json 內容必須是陣列")
        tmp = calendar_dir / f"{year}.json.tmp"
        tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        final = calendar_dir / f"{year}.json"
        tmp.replace(final)
        written.append(final)
    return written


def main(argv=None):
    """命令列工具：以 --sync 明確下載行事曆並印出寫入路徑。"""
    parser = argparse.ArgumentParser(
        description="臺灣行事曆：下載年度 JSON 供抵達日分類使用")
    parser.add_argument("--sync", action="store_true",
                        help="下載今年與明年的行事曆 JSON")
    args = parser.parse_args(argv)
    if args.sync:
        for path in sync_calendars():
            print(path)


if __name__ == "__main__":
    main()
