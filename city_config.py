"""城市註冊表：全系統唯一的城市代碼、別名、行政區與地理邊界來源。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CityDefinition:
    code: str
    name: str
    aliases: tuple[str, ...]
    districts: tuple[str, ...]
    bounds: tuple[float, float, float, float]


CITIES = {
    "taipei": CityDefinition("taipei", "臺北市", ("臺北市", "台北市", "臺北", "台北"),
        ("松山區", "信義區", "大安區", "中山區", "中正區", "大同區", "萬華區",
         "文山區", "南港區", "內湖區", "士林區", "北投區"),
        (24.8, 25.3, 121.3, 121.8)),
    "new_taipei": CityDefinition("new_taipei", "新北市", ("新北市", "新北"),
        ("板橋區", "三重區", "中和區", "永和區", "新莊區", "新店區", "土城區",
         "蘆洲區", "汐止區", "樹林區", "鶯歌區", "三峽區", "淡水區", "瑞芳區",
         "五股區", "泰山區", "林口區", "深坑區", "石碇區", "坪林區", "三芝區",
         "石門區", "八里區", "平溪區", "雙溪區", "貢寮區", "金山區", "萬里區",
         "烏來區"), (24.5, 25.4, 121.2, 122.1)),
}


def normalize_city(value: str | None) -> str | None:
    """把城市代碼或別名轉成唯一代碼；空值回傳 None，未知城市拋錯。"""
    if value is None or not str(value).strip():
        return None
    cleaned = str(value).strip()
    for code, definition in CITIES.items():
        if cleaned == code or cleaned in definition.aliases:
            return code
    raise ValueError("不支援的城市")


def city_name(code: str) -> str:
    """回傳寫入資料庫使用的官方城市名稱。"""
    return CITIES[code].name


def validate_city_district(city: str, district: str | None) -> None:
    """確認行政區屬於指定城市；不屬於時回傳明確錯誤。"""
    if district and district not in CITIES[city].districts:
        raise ValueError(f"{district} 不屬於 {CITIES[city].name}")


def public_city_options(new_taipei_enabled: bool) -> list[dict]:
    """依功能旗標回傳前端可選城市與行政區清單。"""
    codes = ["taipei"] + (["new_taipei"] if new_taipei_enabled else [])
    return [{"code": code, "name": CITIES[code].name,
             "districts": list(CITIES[code].districts)} for code in codes]
