"""一键健康检查（路线图 P1-5）：三服务各自 ready 与否，一条命令看全。

    python3 healthcheck.py            # 检查一次,人可读输出,任一必检项挂 → 退出码 1
    python3 healthcheck.py --wait 60  # 轮询直到全 ready 或超时(compose up 后接这条最顺手)

检查口径（全部纯 stdlib，读 .env 保持与服务同配置）：
- api     : GET /api/health == 200（api_server 的免鉴权探针）
- console : GET / 返回任意 2xx（登录页或工作台，说明进程活着）
- agent   : 仅 ADAPTER=kf 时检查——回调端口对无签名 GET 回 403/400 即为"在监听"
            （MOCK/未配 kf 时跳过，本地开发不误报）
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import load_env_file


def _http_status(url: str, timeout: float = 3.0) -> int | None:
    """返回 HTTP 状态码；连接层失败（拒连/超时/DNS）返回 None。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code  # 4xx/5xx 也是"进程活着"的证据,由各检查项自行判定
    except (urllib.error.URLError, OSError, ValueError):
        return None


def build_checks() -> list[tuple[str, str, "callable"]]:
    """按当前配置组装检查项：(名称, 描述, 检查函数->(ok, detail))。"""
    env = os.environ.get
    checks = []

    api_host = env("API_HOST", "127.0.0.1")
    api_host = "127.0.0.1" if api_host == "0.0.0.0" else api_host
    api_url = f"http://{api_host}:{env('API_PORT', '8080')}/api/health"

    def check_api():
        st = _http_status(api_url)
        return st == 200, f"{api_url} → {st if st is not None else '连不上'}"
    checks.append(("api", "运营 REST API", check_api))

    console_host = env("CONSOLE_HOST", "127.0.0.1")
    console_host = "127.0.0.1" if console_host == "0.0.0.0" else console_host
    console_url = f"http://{console_host}:{env('CONSOLE_PORT', '8090')}/"

    def check_console():
        st = _http_status(console_url)
        return st is not None and 200 <= st < 300, f"{console_url} → {st if st is not None else '连不上'}"
    checks.append(("console", "Web 工作台", check_console))

    if env("ADAPTER") == "kf":
        cb_url = (f"http://127.0.0.1:{env('WECOM_CALLBACK_PORT', '9000')}"
                  f"{env('WECOM_CALLBACK_PATH', '/wecom/callback')}")

        def check_agent():
            st = _http_status(cb_url)
            # 无签名 GET 预期 400/403;能回任何 HTTP 状态都说明回调服务器在监听
            return st is not None, f"{cb_url} → {st if st is not None else '连不上'}"
        checks.append(("agent", "微信客服回调", check_agent))

    return checks


def run_once(checks) -> bool:
    all_ok = True
    for name, desc, fn in checks:
        ok, detail = fn()
        print(f"  {'✓' if ok else '✗'} {name:8s} {desc}: {detail}")
        all_ok = all_ok and ok
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="三服务一键健康检查")
    parser.add_argument("--wait", type=int, default=0, metavar="秒",
                        help="轮询直到全 ready 或超时(每 2 秒一轮)")
    args = parser.parse_args()

    load_env_file()  # 与服务同一配置口径(端口/adapter 开关)
    checks = build_checks()
    skipped = "" if os.environ.get("ADAPTER") == "kf" else "(agent 未启用 kf,跳过回调检查)"

    deadline = time.time() + args.wait
    while True:
        print(f"[健康检查] {time.strftime('%H:%M:%S')} {skipped}")
        if run_once(checks):
            print("全部 ready ✓")
            return 0
        if time.time() >= deadline:
            print("存在未 ready 服务 ✗" + (f"(已等待 {args.wait}s)" if args.wait else ""))
            return 1
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
