"""极简 pytest 垫片：只实现本仓库测试用到的 fixture/raises/mark。

环境里没有 pip/pytest，项目本身零第三方依赖；这个垫片按测试目录的用法
提供最小支持（tmp_path / clock / platform 夹具、pytest.raises），直接
收集并执行 tests/ 下的 test_* 模块，按 pytest 风格报告。
"""

from __future__ import annotations

import importlib
import inspect
import pathlib
import tempfile
import traceback
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- 最小 pytest 替身 -------------------------------------------------------
pytest = types.ModuleType("pytest")


class _Raises:
    def __init__(self, exc_type):
        self.exc_type = exc_type
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError(f"DID NOT RAISE {self.exc_type.__name__}")
        if not issubclass(exc_type, self.exc_type):
            return False
        self.value = exc
        return True


def raises(exc_type):
    return _Raises(exc_type)


class _Mark:
    def __getattr__(self, name):
        def deco(obj=None, *args, **kwargs):
            if obj is None:
                return lambda inner: inner
            return obj

        return deco


pytest.raises = raises
pytest.mark = _Mark()
sys.modules["pytest"] = pytest

# 部分测试用 `from support import ...`（tests/ 在根 pyproject 的 pythonpath 里）。
sys.path.insert(0, str(ROOT / "tests"))
import dataclasses  # noqa: E402

from hydrorefuel.app import build_platform  # noqa: E402
from hydrorefuel.clock import ManualClock  # noqa: E402
from hydrorefuel.config import (  # noqa: E402
    GunPolicies,
    GunProfile,
    default_config,
)


def make_fixtures(tmpdir):
    clock = ManualClock()
    platform = build_platform(default_config(), clock=clock, storage_root=str(tmpdir / "state"))

    cfg = default_config()
    extend_cfg = dataclasses.replace(
        cfg,
        dispenser=dataclasses.replace(cfg.dispenser, gun_type="TK35"),
        guns=GunPolicies(
            {"TK70": cfg.guns.get("TK70"), "TK35": GunProfile("TK35", "extend", 10.0, 1)}
        ),
    )
    extend_gun_platform = build_platform(
        extend_cfg, clock=clock, storage_root=str(tmpdir / "state-extend")
    )
    return {
        "clock": clock,
        "platform": platform,
        "extend_gun_platform": extend_gun_platform,
        "tmp_path": tmpdir,
    }


def main() -> int:
    failures = []
    passed = 0
    test_files = sorted((ROOT / "tests").glob("test_*.py"))
    for path in test_files:
        modname = path.stem
        mod = importlib.import_module(modname)
        for name, fn in vars(mod).items():
            if not name.startswith("test_") or not callable(fn):
                continue
            sig = inspect.signature(fn)
            with tempfile.TemporaryDirectory() as raw:
                fixtures = make_fixtures(pathlib.Path(raw))
                kwargs = {}
                for param in sig.parameters:
                    if param not in fixtures:
                        raise RuntimeError(f"未知夹具 {param}（{modname}.{name}）")
                    kwargs[param] = fixtures[param]
                try:
                    fn(**kwargs)
                    passed += 1
                except Exception:  # noqa: BLE001
                    failures.append((modname, name, traceback.format_exc()))
    for modname, name, tb in failures:
        print(f"FAIL {modname}.{name}\n{tb}")
    print(f"{passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
