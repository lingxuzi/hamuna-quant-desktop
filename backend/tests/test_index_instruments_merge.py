"""指数维表合并语义: 源少给时不删已知行。

源端指数列表不稳定 (实测 MAC 的 ZS 分类从 607 掉到 100)。整表覆盖会把已知
指数从维表里删掉 —— 维表同时是日K同步的标的全集, 删了就永远不再刷新,
且历史行情还在但取不到名称。
"""

import polars as pl

from app.data_providers.repository import DataStore, KlineRepository


def _row(symbol: str, name: str, asset_type: str = "index") -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "code": symbol.split(".")[0],
        "asset_type": asset_type,
    }


def _repo(tmp_path) -> KlineRepository:
    return KlineRepository(DataStore(data_dir=tmp_path))


def _saved(tmp_path) -> pl.DataFrame:
    return pl.read_parquet(tmp_path / "instruments_index" / "instruments_index.parquet")


def test_source_shrinking_does_not_drop_known_rows(tmp_path):
    """核心回归: 源只给 1 只时, 既有 3 只必须全部保留。"""
    repo = _repo(tmp_path)
    repo.save_index_instruments(pl.DataFrame([_row("A.SH", "甲"), _row("B.SZ", "乙"), _row("C.SZ", "丙")]))

    repo.save_index_instruments(pl.DataFrame([_row("A.SH", "甲")]))

    out = _saved(tmp_path)
    assert out.height == 3
    assert set(out["symbol"].to_list()) == {"A.SH", "B.SZ", "C.SZ"}


def test_new_rows_are_appended(tmp_path):
    repo = _repo(tmp_path)
    repo.save_index_instruments(pl.DataFrame([_row("A.SH", "甲")]))
    repo.save_index_instruments(pl.DataFrame([_row("B.SZ", "乙")]))

    assert set(_saved(tmp_path)["symbol"].to_list()) == {"A.SH", "B.SZ"}


def test_new_value_overrides_old_value(tmp_path):
    repo = _repo(tmp_path)
    repo.save_index_instruments(pl.DataFrame([_row("A.SH", "旧名")]))
    repo.save_index_instruments(pl.DataFrame([_row("A.SH", "新名")]))

    out = _saved(tmp_path)
    assert out.height == 1
    assert out["name"][0] == "新名"


def test_null_field_in_new_keeps_old_value(tmp_path):
    """源偶尔漏名字段: null 不能把已有名称抹掉。"""
    repo = _repo(tmp_path)
    repo.save_index_instruments(pl.DataFrame([_row("A.SH", "甲")]))

    new = pl.DataFrame([{"symbol": "A.SH", "name": None, "code": "A", "asset_type": "index"}])
    repo.save_index_instruments(new)

    out = _saved(tmp_path)
    assert out.height == 1
    assert out["name"][0] == "甲"


def test_first_write_has_no_existing_file(tmp_path):
    repo = _repo(tmp_path)
    repo.save_index_instruments(pl.DataFrame([_row("A.SH", "甲"), _row("B.SZ", "乙")]))

    out = _saved(tmp_path)
    assert out.height == 2
    assert out["symbol"].to_list() == sorted(out["symbol"].to_list())


def test_empty_frame_is_noop(tmp_path):
    repo = _repo(tmp_path)
    repo.save_index_instruments(pl.DataFrame([_row("A.SH", "甲")]))
    repo.save_index_instruments(pl.DataFrame())

    assert _saved(tmp_path).height == 1
