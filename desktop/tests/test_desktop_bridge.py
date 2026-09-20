"""桌面桥的回归测试。

用户报告过「打开 BabelDOC 后显示未响应」：``DesktopBridge`` 曾把 pywebview 的
``Window`` 对象挂在公开实例属性上，pywebview 生成 JS 对象时会递归遍历
``dir(js_api)``，遇到 ``Window`` 与 .NET 控件后无限展开，把进程卡死，同时
``_pywebviewready`` 事件永远不触发，会话令牌无法注入，前端所有请求被 403 拒绝。
"""

from __future__ import annotations

from babeldoc_workbench import app as app_module


def test_bridge_exposes_only_callables():
    """pywebview 会递归导出公开属性，因此桥对象只能暴露可调用成员。"""
    bridge = app_module.DesktopBridge()

    public_names = [name for name in dir(bridge) if not name.startswith("_")]
    assert public_names, "桥至少要暴露 choose_directory 与 finish_close"
    for name in public_names:
        assert callable(getattr(bridge, name)), f"不应暴露非可调用属性：{name}"


def test_bridge_keeps_no_public_instance_state():
    """窗口与回调必须放在模块级状态里，不能挂在实例上。"""
    bridge = app_module.DesktopBridge()

    assert bridge.__dict__ == {}


def test_bridge_state_is_module_level():
    """共享状态用模块级字典保存，键固定为 window / allow_close。"""
    assert set(app_module._BRIDGE_STATE) == {"window", "allow_close"}


def test_bridge_methods_survive_empty_state():
    """窗口尚未创建时调用桥方法不能抛异常。"""
    saved = dict(app_module._BRIDGE_STATE)
    app_module._BRIDGE_STATE.update({"window": None, "allow_close": None})
    try:
        bridge = app_module.DesktopBridge()
        assert bridge.choose_directory() is None
        assert bridge.finish_close() is True
    finally:
        app_module._BRIDGE_STATE.update(saved)


def test_bridge_choose_directory_uses_registered_window():
    """选择目录要使用注册进模块状态的窗口对象。"""

    class FakeWindow:
        def __init__(self) -> None:
            self.calls: list[object] = []

        def create_file_dialog(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return ["D:\\目标目录"]

    saved = dict(app_module._BRIDGE_STATE)
    fake = FakeWindow()
    app_module._BRIDGE_STATE["window"] = fake
    try:
        assert app_module.DesktopBridge().choose_directory("D:\\起始") == "D:\\目标目录"
    finally:
        app_module._BRIDGE_STATE.update(saved)
    assert fake.calls, "应当调用窗口的文件对话框"
