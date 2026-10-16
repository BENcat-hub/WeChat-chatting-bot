"""不需要 pytest 的插件契约测试直跑器。

背景：astrbot venv 里没有 pytest（`No module named pytest`），但
`astrbot_plugin_image_companion/tests/*.py` 是 pytest 风格：
模块级 `def test_*`、`monkeypatch`/`tmp_path`/`runtime` 等 fixture、异步测试、`import pytest`。

本脚本提供一个最小 pytest 兼容层（fixture 解析 + 异步执行 + pytest 桩），
用来在没有 pytest 的环境里做回归。用法：

    cd astrbot/data/plugins
    <venv python> ../../../scripts/run_plugin_tests.py
    <venv python> ../../../scripts/run_plugin_tests.py pkg.tests.test_a pkg.tests.test_b
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import io
import pathlib
import sys
import tempfile
import traceback
import types
import unittest

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

HERE = pathlib.Path(__file__).resolve()
STACK = HERE.parent.parent
PLUGINS = STACK / "astrbot" / "data" / "plugins"
sys.path.insert(0, str(PLUGINS))
# 有些测试模块互相 import（例如 test_tool_photo_delivery 导入同目录的 harness），
# 所以各插件的 tests 目录本身也要在 sys.path 上（对应 pytest 的 rootdir 行为）。
for _tests_dir in PLUGINS.glob("*/tests"):
    sys.path.insert(0, str(_tests_dir))

# tmp_path 必须落在「没有 8.3 短名」的目录下：系统 TEMP 常返回 C:\Users\ADMINI~1.DES\...，
# 而插件代码会把路径 realpath 成长名，导致 `Path(candidate) == tmp_path.resolve()` 这类
# 断言在 Windows 上假失败（已确认与本仓库代码无关，纯环境）。
_TMP_BASE = STACK / "logs" / ".pytest-tmp"
_TMP_BASE.mkdir(parents=True, exist_ok=True)
TMP_BASE = _TMP_BASE


def install_pytest_stub() -> None:
    """没有 pytest 时装一个最小桩，好让 `import pytest` 的测试模块能加载。"""
    if "pytest" in sys.modules:
        return
    try:
        import pytest  # noqa: F401

        return
    except ModuleNotFoundError:
        pass

    class Skipped(Exception):
        pass

    class _Approx:
        def __init__(self, value: object, rel: float = 1e-6, abs: float = 1e-12) -> None:
            self.value = value
            self.rel = rel
            self.abs = abs

        def __eq__(self, other: object) -> bool:
            try:
                return abs(float(other) - float(self.value)) <= max(
                    self.abs, self.rel * abs(float(self.value))
                )
            except Exception:
                return other == self.value

        def __repr__(self) -> str:  # pragma: no cover
            return f"approx({self.value!r})"

    class _Mark:
        def parametrize(self, argnames, argvalues, **kwargs):
            names = (
                [a.strip() for a in argnames.split(",")]
                if isinstance(argnames, str)
                else list(argnames)
            )

            def decorator(fn):
                entries = list(getattr(fn, "_pytest_parametrize", []))
                entries.append((names, list(argvalues)))
                fn._pytest_parametrize = entries
                return fn

            return decorator

        def __getattr__(self, name: str):
            def decorator(*args, **kwargs):
                if len(args) == 1 and callable(args[0]) and not kwargs:
                    return args[0]

                def inner(fn):
                    return fn

                return inner

            return decorator

    class _ExcInfo:
        """pytest.raises 的 `as caught:` 对象，提供 .value / .match()。"""

        def __init__(self) -> None:
            self.value: BaseException | None = None

        def match(self, pattern: str) -> bool:
            import re as _re

            return _re.search(pattern, str(self.value)) is not None

        def __str__(self) -> str:  # pragma: no cover
            return str(self.value)

    @contextlib.contextmanager
    def raises(expected, match: str | None = None, **kwargs):
        info = _ExcInfo()
        try:
            yield info
        except expected as exc:
            info.value = exc
            if match is not None and not info.match(match):
                raise AssertionError(f"异常信息不匹配 {match!r}: {exc}") from exc
            return
        except Exception as exc:  # pragma: no cover - 诊断用
            raise AssertionError(f"期望 {expected}，实际 {type(exc).__name__}: {exc}") from exc
        raise AssertionError(f"期望抛出 {expected}，但没有异常")

    def fixture(*args, **kwargs):
        if len(args) == 1 and callable(args[0]):
            fn = args[0]
            fn._pytest_fixture = True
            return fn

        def decorator(fn):
            fn._pytest_fixture = True
            return fn

        return decorator

    def skip(reason: str = "", allow_module_level: bool = False):
        raise Skipped(reason)

    def importorskip(name: str, *a, **kw):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            raise Skipped(f"missing optional dependency {name}") from exc

    def fail(msg: str = ""):
        raise AssertionError(msg)

    stub = types.ModuleType("pytest")
    stub.raises = raises
    stub.approx = _Approx
    stub.mark = _Mark()
    stub.fixture = fixture
    stub.skip = skip
    stub.importorskip = importorskip
    stub.Skipped = Skipped
    stub.fail = fail
    sys.modules["pytest"] = stub


class MiniMonkeyPatch:
    """最小 monkeypatch 桩：setattr / delattr / undo。"""

    def __init__(self) -> None:
        self._undo: list[tuple[object, str, object]] = []
        self._undo_del: list[tuple[object, str]] = []

    def setattr(self, target: object, name: str, value: object, raising: bool = True) -> None:
        if hasattr(target, name):
            self._undo.append((target, name, getattr(target, name)))
        elif raising is False:
            self._undo_del.append((target, name))
        else:
            self._undo_del.append((target, name))
        setattr(target, name, value)

    def delattr(self, target: object, name: str) -> None:
        if hasattr(target, name):
            self._undo.append((target, name, getattr(target, name)))
            delattr(target, name)

    def undo(self) -> None:
        for target, name, old in reversed(self._undo):
            setattr(target, name, old)
        for target, name in reversed(self._undo_del):
            with contextlib.suppress(AttributeError):
                delattr(target, name)
        self._undo.clear()
        self._undo_del.clear()


class Capsys:
    """最小 capsys 桩：把 stdout/stderr 换成 StringIO，readouterr 返回内容。"""

    class _Capture:
        def __init__(self, out: str, err: str) -> None:
            self.out = out
            self.err = err

    def __init__(self) -> None:
        import io

        self._out = io.StringIO()
        self._err = io.StringIO()
        self._saved: tuple[object, object] | None = None

    def start(self) -> None:
        self._saved = (sys.stdout, sys.stderr)
        sys.stdout, sys.stderr = self._out, self._err

    def stop(self) -> None:
        if self._saved:
            sys.stdout, sys.stderr = self._saved
            self._saved = None

    def readouterr(self) -> "Capsys._Capture":
        cap = Capsys._Capture(self._out.getvalue(), self._err.getvalue())
        self._out.seek(0)
        self._out.truncate(0)
        self._err.seek(0)
        self._err.truncate(0)
        return cap


def _fixture_factories(module: types.ModuleType) -> dict[str, object]:
    out: dict[str, object] = {}
    for name, obj in vars(module).items():
        if callable(obj) and (
            getattr(obj, "_pytest_fixture", False)
            or inspect.isgeneratorfunction(obj)
        ):
            out[name] = obj
    return out


def call_with_fixtures(fn, module, monkeypatch, tmp_path, capsys, depth=0, overrides=None):
    overrides = dict(overrides or {})
    kwargs = {}
    teardowns: list[object] = []
    factories = _fixture_factories(module)
    for p in inspect.signature(fn).parameters:
        if p in overrides:
            kwargs[p] = overrides[p]
        elif p == "monkeypatch":
            kwargs[p] = monkeypatch
        elif p == "tmp_path":
            kwargs[p] = tmp_path
        elif p == "capsys":
            kwargs[p] = capsys
        elif p == "recwarn":
            kwargs[p] = types.SimpleNamespace(list=[])
        elif p in factories and depth < 6:
            value, created = call_with_fixtures(
                factories[p], module, monkeypatch, tmp_path, capsys, depth + 1
            )
            teardowns.extend(created)
            if inspect.isgenerator(value):
                gen = value
                try:
                    value = next(gen)
                except StopIteration:
                    value = None
                teardowns.append(gen)
            kwargs[p] = value
        else:
            raise RuntimeError(f"未支持的 fixture: {p}")
    result = fn(**kwargs)
    if inspect.isgenerator(result):
        gen = result
        try:
            result = next(gen)
        except StopIteration:
            result = None
        teardowns.append(gen)
    return result, teardowns


def parametrize_cases(fn) -> list[tuple[dict, str]]:
    """把 `@pytest.mark.parametrize` 展开成 (参数覆盖, 用例标签) 列表。"""
    entries = getattr(fn, "_pytest_parametrize", None)
    if not entries:
        return [({}, "")]
    combos: list[tuple[dict, str]] = [({}, "")]
    for names, values in entries:
        expanded: list[tuple[dict, str]] = []
        for base, label in combos:
            for value in values:
                if len(names) == 1:
                    pair = (names[0], value)
                else:
                    seq = value if isinstance(value, (tuple, list)) else (value,)
                    if len(seq) != len(names):
                        raise RuntimeError(f"parametrize 参数个数不匹配: {names} <- {value!r}")
                    pair = None
                case = dict(base)
                if pair is not None:
                    case[pair[0]] = pair[1]
                    part = f"{pair[0]}={pair[1]!r}"
                else:
                    case.update(dict(zip(names, seq)))
                    part = " ".join(f"{n}={case[n]!r}" for n in names)
                lbl = (label + " " + part).strip()
                expanded.append((case, lbl[:80]))
        combos = expanded
    return combos


def run_testcase_classes(
    module, *, verbose: bool = False, only: str | None = None
) -> tuple[int, int, list[str]]:
    """跑模块里 class 式用例（unittest.TestCase / IsolatedAsyncioTestCase）。

    旧版只遍历模块级 `test_*` 函数，class 式用例会被**静默跳过**（模块报 0 个用例，
    看起来“全绿”其实一个都没跑）。这里交回 unittest 自己执行，计数合并进总数。

    仓库里的 class 用例是 **pytest 风格**：它们用 `setup_method` / `teardown_method` /
    `setup_class` / `teardown_class` 而不是 `setUp` / `setUpClass`。pytest 两种都支持，
    裸 unittest 只认后者 —— 不补这一步，用例会因为 `setup_method` 没被调用而报
    `AttributeError: ... object has no attribute ...`（假失败，与产品代码无关）。
    """
    class _FixtureForwardingCase(unittest.TestCase):
        """把 pytest 风格的 setup_method/teardown_method 转发给用例方法。"""

        def runTest(self) -> None:  # pragma: no cover - 仅占位，永不执行
            pass

    passed = failed = 0
    skipped: list[str] = []
    for name, obj in sorted(vars(module).items()):
        if not (isinstance(obj, type) and issubclass(obj, unittest.TestCase)):
            continue
        loader = unittest.defaultTestLoader
        selected = [
            test
            for test in loader.loadTestsFromTestCase(obj)
            if not (only and only not in test.id() and only not in name)
        ]
        if not selected:
            continue
        # unittest 原生只认 setUpClass；而 setUpClass 只在「套件级」运行时被调用，
        # 逐个 test.run() 会跳过它（本例会让 self.runtime 不存在）。这里手动补上。
        if "setUpClass" in obj.__dict__:
            try:
                obj.setUpClass()
            except Exception as exc:
                print(f"[FAIL] {obj.__name__}.setUpClass")
                print(f"setUpClass failed: {exc!r}")
                failed += len(selected)
                continue
        class_setup = getattr(obj, "setup_class", None)
        class_teardown = getattr(obj, "teardown_class", None)
        ran_class_setup = False
        for test in selected:
            case_id = test.id()
            if only and only not in case_id and only not in name:
                continue
            method = getattr(obj, test._testMethodName, None)
            setup = getattr(obj, "setup_method", None)
            teardown = getattr(obj, "teardown_method", None)
            if not ran_class_setup and class_setup is not None:
                try:
                    class_setup(obj)
                except Exception as exc:  # setup_class 失败按错误计入
                    print(f"[FAIL] {case_id}")
                    print(f"setup_class failed: {exc!r}")
                    failed += 1
                    continue
                ran_class_setup = True
            result = unittest.TestResult()
            stream = io.StringIO()
            try:
                if setup is not None:
                    setup(method)
            except Exception as exc:
                print(f"[FAIL] {case_id}")
                print(f"setup_method failed: {exc!r}")
                failed += 1
                continue
            try:
                with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                    test.run(result)
            finally:
                if teardown is not None:
                    try:
                        teardown(method)
                    except Exception as exc:
                        print(f"[FAIL] {case_id}")
                        print(f"teardown_method failed: {exc!r}")
                        failed += 1
                        continue
            if result.skipped:
                skipped.append(f"{case_id}: {result.skipped[0][1]}")
                if verbose:
                    print(f"[SKIP] {case_id} ({result.skipped[0][1]})")
            elif result.failures or result.errors:
                failed += 1
                detail = (result.failures + result.errors)[0][1]
                print(f"[FAIL] {case_id}")
                print(detail)
            else:
                passed += 1
                if verbose:
                    print(f"[PASS] {case_id}")
        if ran_class_setup and class_teardown is not None:
            try:
                class_teardown(obj)
            except Exception as exc:
                print(f"[FAIL] {obj.__name__}.teardown_class")
                print(f"teardown_class failed: {exc!r}")
                failed += 1
        if "tearDownClass" in obj.__dict__:
            try:
                obj.tearDownClass()
            except Exception as exc:
                print(f"[FAIL] {obj.__name__}.tearDownClass")
                print(f"tearDownClass failed: {exc!r}")
                failed += 1
    return passed, failed, skipped


def run_module(
    module_name: str, *, verbose: bool = False, only: str | None = None
) -> tuple[int, int, list[str]]:
    module = importlib.import_module(module_name)
    passed = failed = 0
    skipped: list[str] = []
    for name, fn in sorted(vars(module).items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        if only and only not in name:
            continue
        for overrides, label in parametrize_cases(fn):
            case_name = f"{name}[{label}]" if label else name
            mp = MiniMonkeyPatch()
            capsys = Capsys()
            tmp_path = pathlib.Path(tempfile.mkdtemp(prefix="plugin-test-", dir=str(TMP_BASE)))
            teardowns: list[object] = []
            capsys.start()
            status = "pass"
            detail = ""
            try:
                value, created = call_with_fixtures(
                    fn, module, mp, tmp_path, capsys, 0, overrides
                )
                teardowns.extend(created)
                if inspect.iscoroutine(value):
                    asyncio.run(value)
            except Exception as exc:
                if type(exc).__name__ == "Skipped" or "missing optional dependency" in str(exc):
                    status = "skip"
                    detail = str(exc)
                else:
                    status = "fail"
                    detail = traceback.format_exc()
            finally:
                for item in reversed(teardowns):
                    with contextlib.suppress(Exception):
                        next(item)  # type: ignore[arg-type]
                capsys.stop()
                mp.undo()
            # 注意：一定要在 capsys.stop() 之后再打印，否则输出被 capsys 缓冲吞掉
            if status == "pass":
                passed += 1
                if verbose:
                    print(f"[PASS] {module_name}::{case_name}")
            elif status == "skip":
                skipped.append(f"{module_name}::{case_name}: {detail}")
                if verbose:
                    print(f"[SKIP] {module_name}::{case_name} ({detail})")
            else:
                failed += 1
                print(f"[FAIL] {module_name}::{case_name}")
                print(detail)
    cls_passed, cls_failed, cls_skipped = run_testcase_classes(module, verbose=verbose, only=only)
    return passed + cls_passed, failed + cls_failed, skipped + cls_skipped


def main() -> int:
    install_pytest_stub()
    only = None
    argv = sys.argv[1:]
    if "--only" in argv:
        i = argv.index("--only")
        only = argv[i + 1]
        del argv[i : i + 2]
    if argv:
        targets = list(argv)
    else:
        targets = [
            p.replace("/", ".").replace("\\", ".")[:-3]
            for p in sorted(
                str(path.relative_to(PLUGINS)).replace("\\", "/")
                for path in PLUGINS.glob("*/tests/test_*.py")
            )
        ]
    total_pass = total_fail = 0
    all_skips: list[str] = []
    for target in targets:
        try:
            p, f, s = run_module(target, only=only)
        except Exception:
            print(f"[FAIL] {target} (导入失败)")
            traceback.print_exc()
            total_fail += 1
            continue
        total_pass += p
        total_fail += f
        all_skips.extend(s)
    if all_skips:
        print("\n跳过（缺少可选依赖）：")
        for item in all_skips:
            print(f"  - {item}")
    print(f"\n结果：{total_pass} passed / {total_fail} failed / {len(all_skips)} skipped")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

