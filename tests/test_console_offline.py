"""admin_console 离线测试：ConsoleApp 纯逻辑层，注入 fake fetcher，不起 socket、不走网络。"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_console import _COOKIE, ConsoleApp  # noqa: E402


def _fake_fetcher(calls):
    """记录转发调用，返回固定成功响应。"""
    def fetcher(method, url, body, headers):
        calls.append({"method": method, "url": url, "body": body, "headers": headers})
        return 200, b'{"ok": true, "from": "fake-api"}'
    return fetcher


def _login(app, password):
    status, _, body, extra = app.handle("POST", "/login", json.dumps({"password": password}).encode(), {})
    cookie = ""
    set_cookie = extra.get("Set-Cookie", "")
    if set_cookie:
        cookie = set_cookie.split(";")[0]
    return status, body, cookie


def test_login_required_and_flow():
    app = ConsoleApp(admin_token="tok", password="pw123", fetcher=_fake_fetcher([]))
    # 未登录：/ 返回登录页
    status, ctype, body, _ = app.handle("GET", "/", b"", {})
    assert status == 200 and "text/html" in ctype
    assert "登录".encode("utf-8") in body or b"login" in body.lower()
    # 未登录：/api 代理 401
    status, _, _, _ = app.handle("GET", "/api/metrics", b"", {})
    assert status == 401
    # 错口令 401
    status, _, _ = _login(app, "wrong")
    assert status == 401
    # 对口令 → 拿 cookie
    status, _, cookie = _login(app, "pw123")
    assert status == 200 and cookie.startswith(_COOKIE + "=")
    # 带 cookie：/ 返回工作台页
    status, _, body, _ = app.handle("GET", "/", b"", {"cookie": cookie})
    assert status == 200 and "工作台".encode("utf-8") in body
    print("test_login_required_and_flow ok")


def test_proxy_injects_token_and_strips_from_client():
    calls = []
    app = ConsoleApp(api_base="http://api.internal:8080", admin_token="secret-tok",
                     password="pw", fetcher=_fake_fetcher(calls))
    _, _, cookie = _login(app, "pw")
    status, _, body, _ = app.handle("GET", "/api/conversations", b"", {"cookie": cookie})
    assert status == 200 and b"fake-api" in body
    assert calls[0]["url"] == "http://api.internal:8080/api/conversations"
    assert calls[0]["headers"].get("X-Admin-Token") == "secret-tok"
    # 前端页面里绝不能出现 admin token
    _, _, page, _ = app.handle("GET", "/", b"", {"cookie": cookie})
    assert b"secret-tok" not in page
    print("test_proxy_injects_token_and_strips_from_client ok")


def test_csrf_header_required_for_writes():
    calls = []
    app = ConsoleApp(admin_token="t", password="pw", fetcher=_fake_fetcher(calls))
    _, _, cookie = _login(app, "pw")
    # 写操作缺 X-Requested-With → 403（跨站表单打不进来）
    status, _, _, _ = app.handle("POST", "/api/conversations/u1/takeover", b"", {"cookie": cookie})
    assert status == 403 and not calls
    # 带上即可
    status, _, _, _ = app.handle("POST", "/api/conversations/u1/takeover", b"",
                                 {"cookie": cookie, "x-requested-with": "fetch"})
    assert status == 200 and len(calls) == 1
    # GET 不要求该头（浏览器地址栏/刷新也要能用）
    status, _, _, _ = app.handle("GET", "/api/metrics", b"", {"cookie": cookie})
    assert status == 200
    print("test_csrf_header_required_for_writes ok")


def test_dev_mode_no_password():
    calls = []
    app = ConsoleApp(admin_token="", password="", fetcher=_fake_fetcher(calls))
    # 无口令：直接出工作台页 + 代理放行（开发模式）
    status, _, body, _ = app.handle("GET", "/", b"", {})
    assert status == 200 and "工作台".encode("utf-8") in body
    status, _, _, _ = app.handle("GET", "/api/metrics", b"", {})
    assert status == 200
    # 无 admin_token 时不注入头
    assert "X-Admin-Token" not in calls[0]["headers"]
    print("test_dev_mode_no_password ok")


def test_logout_invalidates_cookie():
    app = ConsoleApp(admin_token="t", password="pw", fetcher=_fake_fetcher([]))
    _, _, cookie = _login(app, "pw")
    status, _, _, _ = app.handle("POST", "/logout", b"", {"cookie": cookie})
    assert status == 200
    status, _, _, _ = app.handle("GET", "/api/metrics", b"", {"cookie": cookie})
    assert status == 401
    print("test_logout_invalidates_cookie ok")


def test_fetcher_error_becomes_502():
    def broken(method, url, body, headers):
        return 502, b'{"error": "api unreachable"}'
    app = ConsoleApp(admin_token="t", password="pw", fetcher=broken)
    _, _, cookie = _login(app, "pw")
    status, _, body, _ = app.handle("GET", "/api/metrics", b"", {"cookie": cookie})
    assert status == 502 and b"unreachable" in body
    print("test_fetcher_error_becomes_502 ok")


def test_unknown_path_404_and_bad_login_body():
    app = ConsoleApp(admin_token="t", password="pw", fetcher=_fake_fetcher([]))
    status, _, _, _ = app.handle("GET", "/etc/passwd", b"", {})
    assert status == 404
    # 非法 JSON body 不崩、按空口令处理 → 401
    status, _, _, _ = app.handle("POST", "/login", b"not-json{{", {})
    assert status == 401
    print("test_unknown_path_404_and_bad_login_body ok")


if __name__ == "__main__":
    test_login_required_and_flow()
    test_proxy_injects_token_and_strips_from_client()
    test_csrf_header_required_for_writes()
    test_dev_mode_no_password()
    test_logout_invalidates_cookie()
    test_fetcher_error_becomes_502()
    test_unknown_path_404_and_bad_login_body()
    print("admin_console 离线测试全部通过")
