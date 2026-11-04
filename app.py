"""SkillHub 桌面端入口.

    python app.py              正常启动
    python app.py --selftest   无窗口自检: 加载界面 -> 检查 DOM -> 打印结果 -> 退出
"""

from __future__ import annotations

import sys
import time
import traceback

import webview

from skillhub import api
from skillhub.api import Bridge
from skillhub.paths import UI_DIR, ensure_dirs


def run_selftest(window: webview.Window) -> None:
    """窗口起来后跑一段 JS, 验证界面与桥接都活着."""
    time.sleep(2.5)
    checks = {
        "title": "document.title",
        "navItems": "document.querySelectorAll('.nav-item').length",
        "skillRows": "document.querySelectorAll('#skillTable tbody tr').length",
        "mcpRows": "document.querySelectorAll('#mcpTable tbody tr').length",
        "agentCards": "document.querySelectorAll('.agent-card').length",
        "healthItems": "document.querySelectorAll('.health-item').length",
        "autoSyncBtn": "document.getElementById('btnAutoSync') ? document.getElementById('btnAutoSync').textContent : '<缺失>'",
        "syncAllBtn": "document.getElementById('btnSyncAll') ? document.getElementById('btnSyncAll').textContent : '<缺失>'",
        "pageSub": "document.getElementById('pageSub').textContent",
        "toast": "document.getElementById('toast').className",
        "tipEl": "!!document.getElementById('tip')",
        "detailEl": "!!document.getElementById('detail')",
        "jsError": "window.__lastError || ''",
    }
    ok = True
    print("\n===== SkillHub 自检 =====")
    for name, js in checks.items():
        try:
            val = window.evaluate_js(js)
        except Exception as exc:
            val = f"<求值失败: {exc}>"
            ok = False
        print(f"  {name:12} = {val}")

    # 检查有没有渲染出空表格(通常意味着 bridge 报错)
    try:
        empty = window.evaluate_js("document.querySelectorAll('.empty').length")
        sub = window.evaluate_js("document.getElementById('pageSub').textContent")
        if "失败" in str(sub):
            print("  ! 界面报告初始化失败:", sub)
            ok = False
        print(f"  emptyBlocks  = {empty}")
    except Exception:
        ok = False

    print("===== 自检结束:", "通过" if ok else "有问题", "=====\n")
    window.destroy()


def run_shot(window: webview.Window) -> None:
    """把窗口置顶后截屏, 用于开发期核对视觉效果."""
    import tempfile
    from pathlib import Path

    time.sleep(2.5)
    try:
        window.show()
        window.restore()
        window.maximize()
        window.on_top = True
    except Exception as exc:
        print(f"窗口调整失败: {exc}")
    time.sleep(1.2)
    try:
        print(
            f"窗口几何: x={window.x} y={window.y} w={window.width} h={window.height} "
            f"on_top={getattr(window, 'on_top', '?')}"
        )
    except Exception as exc:
        print(f"读取几何失败: {exc}")
    try:
        from PIL import ImageGrab

        for view in ("overview", "skills", "mcp", "paths"):
            try:
                window.evaluate_js(
                    f"document.querySelector('.nav-item[data-view=\"{view}\"]').click()"
                )
            except Exception as exc:
                print(f"切换 {view} 失败: {exc}")
            time.sleep(0.9)
            out = Path(tempfile.gettempdir()) / f"skillhub-{view}.png"
            ImageGrab.grab().save(out)
            print(f"SHOT[{view}]: {out}")

        # 额外截一张「全量同步」二次确认态
        try:
            window.evaluate_js("document.querySelector('.nav-item[data-view=\"overview\"]').click()")
            time.sleep(0.5)
            window.evaluate_js("document.getElementById('btnSyncAll').click()")
            time.sleep(0.6)
            out = Path(tempfile.gettempdir()) / "skillhub-confirm.png"
            ImageGrab.grab().save(out)
            print(f"SHOT[confirm]: {out}")
        except Exception as exc:
            print(f"确认态截图失败: {exc}")

        # 删除按钮的二次确认态: 只点一下(仅进入待确认), 不会真删
        try:
            window.evaluate_js("document.querySelector('.nav-item[data-view=\"skills\"]').click()")
            time.sleep(0.5)
            window.evaluate_js("document.querySelector('[data-delskill]').click()")
            time.sleep(0.5)
            out = Path(tempfile.gettempdir()) / "skillhub-delconfirm.png"
            ImageGrab.grab().save(out)
            print(f"SHOT[delconfirm]: {out}")
        except Exception as exc:
            print(f"删除确认态截图失败: {exc}")

        # 悬停说明
        try:
            window.evaluate_js(
                "document.querySelector('[data-skillinfo]')"
                ".dispatchEvent(new MouseEvent('mouseover', {bubbles: true}))"
            )
            time.sleep(0.8)
            out = Path(tempfile.gettempdir()) / "skillhub-tip.png"
            ImageGrab.grab().save(out)
            print(f"SHOT[tip]: {out}")
            window.evaluate_js("document.dispatchEvent(new MouseEvent('mousemove'))")
        except Exception as exc:
            print(f"悬停截图失败: {exc}")

        # 详情面板: MCP 那张专挑环境变量最多的 node_repl, 最容易暴露排版问题
        for view, expr, tag in (
            ("skills", "document.querySelector('[data-skillinfo]')", "detail-skill"),
            (
                "mcp",
                "([...document.querySelectorAll('[data-mcpinfo]')].find(x=>x.dataset.mcpinfo==='node_repl')"
                "||document.querySelector('[data-mcpinfo]'))",
                "detail-mcp",
            ),
        ):
            try:
                window.evaluate_js(f"document.querySelector('.nav-item[data-view=\"{view}\"]').click()")
                time.sleep(0.5)
                window.evaluate_js(f"({expr}).click()")
                time.sleep(1.0)
                out = Path(tempfile.gettempdir()) / f"skillhub-{tag}.png"
                ImageGrab.grab().save(out)
                print(f"SHOT[{tag}]: {out}")
                window.evaluate_js("document.querySelector('[data-close]').click()")
            except Exception as exc:
                print(f"{tag} 截图失败: {exc}")
    except Exception as exc:
        print(f"截图失败: {exc}")
    time.sleep(0.5)
    window.destroy()


def main() -> int:
    ensure_dirs()
    selftest = "--selftest" in sys.argv
    shot = "--shot" in sys.argv

    try:
        bridge = Bridge()
        window = webview.create_window(
            "SkillHub",
            url=str(UI_DIR / "index.html"),
            js_api=bridge,
            width=1220,
            height=800,
            min_size=(980, 640),
            background_color="#f6f6f4",
            hidden=selftest,
        )
        # 目录选择框要用窗口对象(create_file_dialog), 创建完再回填
        api.set_window(window)
        if selftest:
            webview.start(run_selftest, args=(window,), debug=False)
        elif shot:
            webview.start(run_shot, args=(window,), debug=False)
        else:
            webview.start(debug=False)
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
