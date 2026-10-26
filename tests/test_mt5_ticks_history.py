"""Tests for the MT5 tick-history downloader/replayer and the synthetic
fallback tape used to pre-populate the Bookmap-style limit-order DOM cache."""

import time

import pytest

from tmap.core.config import EngineConfig
from tmap.core.engine import OrderFlowEngine
from tmap.core.types import Tick, TradeSide
from tmap.network import mt5_ticks as M


# --------------------------------------------------------------------- synth

def test_generate_synthetic_ticks_shape():
    ticks = M.generate_synthetic_ticks("XAUUSD", initial_price=2000.0,
                                       hours_back=0.01, seed=42)
    assert len(ticks) >= 100
    assert all(t.price > 0 and t.volume > 0 for t in ticks)
    assert all(t.side in (TradeSide.BUY, TradeSide.SELL) for t in ticks)
    # timestamps ascending and ending near "now"
    ts = [t.timestamp for t in ticks]
    assert ts == sorted(ts)
    assert abs(ts[-1] - time.time()) < 5.0


def test_deterministic_with_seed():
    a = M.generate_synthetic_ticks(hours_back=0.01, seed=7)
    b = M.generate_synthetic_ticks(hours_back=0.01, seed=7)
    assert [t.price for t in a] == [t.price for t in b]


# ------------------------------------------------------------- save/load tape

def test_save_load_roundtrip(tmp_path):
    ticks = M.generate_synthetic_ticks(hours_back=0.01, seed=1)
    path = str(tmp_path / "ticks.txt")
    n = M.save_ticks(ticks, path)
    assert n == len(ticks)
    back = M.load_ticks(path)
    assert len(back) == len(ticks)
    assert back[0].price == ticks[0].price
    assert back[-1].side == ticks[-1].side


def test_load_missing_file_returns_empty(tmp_path):
    assert M.load_ticks(str(tmp_path / "nope.txt")) == []


# -------------------------------------------------------------------- replay

def test_replay_populates_dom_cache_and_candles():
    cfg = EngineConfig(symbol="TEST", ticks_per_bar=25)
    eng = OrderFlowEngine(config=cfg)
    ticks = M.generate_synthetic_ticks(symbol="TEST", hours_back=0.01, seed=3)
    n, bars = M.replay_ticks(ticks, eng, chunk_size=100)
    assert n == len(ticks)
    assert eng.dom_cache.ticks_processed == len(ticks)
    assert bars > 0
    walls = eng.dom_cache.get_limit_orders(min_volume=1.0)
    assert len(walls) > 0, "replayed tape must reconstruct limit-order walls"


def test_get_or_generate_falls_back_to_synth_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "mt5_available", lambda: False)
    fpath = str(tmp_path / "ticks_X.txt")
    ticks, source = M.get_or_generate_ticks("X", hours_back=0.01, ticks_file=fpath)
    assert source.startswith("Synthetic")
    assert len(ticks) > 0
    # second call must hit the local cached tape file
    ticks2, source2 = M.get_or_generate_ticks("X", hours_back=0.01, ticks_file=fpath)
    assert source2 == "Local tick cache"
    assert len(ticks2) == len(ticks)


def test_get_or_generate_uses_mocked_mt5(tmp_path, monkeypatch):
    fake = [Tick(timestamp=time.time(), price=100.0, volume=2.0,
                 side=TradeSide.BUY, bid=99.9, ask=100.1)]
    monkeypatch.setattr(M, "mt5_available", lambda: True)
    monkeypatch.setattr(M, "download_ticks", lambda *a, **k: list(fake))
    ticks, source = M.get_or_generate_ticks("Y", hours_back=1.0,
                                            ticks_file=str(tmp_path / "y.txt"))
    assert source == "MT5 (real ticks)"
    assert len(ticks) == 1


def test_get_or_generate_mt5_failure_falls_back(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise M.MT5ConnectionError("terminal offline")
    monkeypatch.setattr(M, "mt5_available", lambda: True)
    monkeypatch.setattr(M, "download_ticks", boom)
    ticks, source = M.get_or_generate_ticks("Z", hours_back=0.01,
                                            ticks_file=str(tmp_path / "z.txt"))
    assert source.startswith("Synthetic")
    assert len(ticks) > 0
