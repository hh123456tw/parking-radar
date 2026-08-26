"""城市註冊表測試：鎖定城市代碼、行政區驗證與功能旗標的公開選項。"""

import pytest

from city_config import normalize_city, public_city_options, validate_city_district


def test_city_registry_normalizes_aliases_and_rejects_cross_city_district():
    assert normalize_city("台北市") == "taipei"
    assert normalize_city("新北") == "new_taipei"
    with pytest.raises(ValueError, match="不屬於"):
        validate_city_district("new_taipei", "信義區")


def test_feature_flag_hides_new_taipei_from_public_options():
    assert [row["code"] for row in public_city_options(False)] == ["taipei"]
    assert [row["code"] for row in public_city_options(True)] == ["taipei", "new_taipei"]
