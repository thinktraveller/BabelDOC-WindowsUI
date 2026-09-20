"""步骤 3 的服务安全边界验证脚本（开发用，可重复执行）。

与计划书里的 curl 验证等价，但由脚本完成，避免手动复制会话令牌：

1. 未携带令牌的接口请求被拒绝；
2. 携带错误令牌被拒绝；
3. 携带正确令牌通过；
4. 非法 Host 被拒绝；
5. 非法 Origin 被拒绝，合法 Origin 通过；
6. 首页（静态页面）免令牌；
7. 服务只监听回环地址（尝试连接本机非回环地址应失败）。
"""

from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "desktop" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from babeldoc_workbench import security  # noqa: E402
from babeldoc_workbench.app import (  # noqa: E402
    SessionInfo,
    create_app,
    serve_in_background,
)


def request(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[int, str]:
    """发起请求并返回 (状态码, 响应体)；HTTP 错误也作为结果返回。"""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:  # 4xx/5xx 也算有效结果
        return error.code, error.read().decode("utf-8", "replace")


def non_loopback_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


def main() -> int:
    session = SessionInfo(token=security.generate_session_token())
    server, port = serve_in_background(create_app(_provider(), session))
    session.port = port  # create_app 之后端口才确定，中间件读取的是同一个对象
    base = f"http://127.0.0.1:{port}"
    checks: list[tuple[str, bool, str]] = []

    status, body = request(f"{base}/api/health")
    checks.append(("未携带令牌被拒绝", status == 403, f"{status} {body[:60]}"))

    status, body = request(
        f"{base}/api/health", headers={security.TOKEN_HEADER: "wrong-token"}
    )
    checks.append(("错误令牌被拒绝", status == 403, f"{status} {body[:60]}"))

    status, body = request(
        f"{base}/api/health",
        headers={
            security.TOKEN_HEADER: session.token,
            "Host": f"127.0.0.1:{port}",
        },
    )
    payload = json.loads(body) if status == 200 else {}
    checks.append(
        (
            "正确令牌通过并返回版本与资源摘要",
            status == 200
            and payload.get("status") in {"ok", "degraded"}
            and "engine_version" in payload,
            f"{status} status={payload.get('status')} engine={payload.get('engine_version')}",
        )
    )

    status, body = request(
        f"{base}/api/health",
        headers={security.TOKEN_HEADER: session.token, "Host": "evil.example.com"},
    )
    checks.append(("非法 Host 被拒绝", status == 403 and "Host" in body, f"{status} {body[:60]}"))

    status, body = request(
        f"{base}/api/session",
        headers={
            security.TOKEN_HEADER: session.token,
            "Origin": "http://evil.example.com",
        },
    )
    checks.append(("非法 Origin 被拒绝", status == 403 and "来源" in body, f"{status} {body[:60]}"))

    status, body = request(
        f"{base}/api/session",
        headers={
            security.TOKEN_HEADER: session.token,
            "Origin": f"http://127.0.0.1:{port}",
        },
    )
    session_payload = json.loads(body) if status == 200 else {}
    checks.append(
        (
            "合法 Origin 通过且会话信息不含令牌",
            status == 200 and session.token not in body and session_payload.get("port") == port,
            f"{status} port={session_payload.get('port')}",
        )
    )

    status, body = request(base + "/")
    checks.append(
        ("首页免令牌可加载", status == 200 and "环境自检" in body, f"{status} {len(body)} 字符")
    )

    exposed: list[str] = []
    for address in non_loopback_addresses():
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(1.0)
        try:
            probe.connect((address, port))
            exposed.append(address)
        except OSError:
            pass
        finally:
            probe.close()
    checks.append(
        (
            "只监听回环地址",
            not exposed,
            f"非回环地址可达：{exposed}" if exposed else "非回环地址均不可达",
        )
    )

    server.should_exit = True

    print("步骤 3 服务安全边界验证")
    print("-" * 60)
    for label, passed, detail in checks:
        print(f"{'✅' if passed else '❌'} {label}：{detail}")
    print("-" * 60)
    failed = [label for label, passed, _ in checks if not passed]
    print(f"总体结论：{'✅ 全部通过' if not failed else '❌ 失败项：' + '、'.join(failed)}")
    return 1 if failed else 0


def _provider():
    """轻量自检结果，避免验证脚本启动工作进程。"""
    from babeldoc_workbench.engine.selfcheck import run_light_checks

    return lambda deep=False: run_light_checks()


if __name__ == "__main__":
    raise SystemExit(main())
