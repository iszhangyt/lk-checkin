#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GLaDOS 自动签到脚本

特性：
- 不走登录流程：直接从配置读取 Cookie
- 签到成功后推送 Telegram
- 推送包含：账号邮箱 / 本次签到积分 / 账户总积分
"""

import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Optional

import requests
import yaml


# ==================== 日志配置 ====================

# 设置 stdout 编码为 UTF-8，解决 Windows 控制台的 Unicode 问题
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ==================== 常量定义 ====================

DEFAULT_BASE_URL = "https://glados.one"
DEFAULT_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json;charset=UTF-8",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
}


# ==================== 工具函数 ====================

def load_config() -> dict:
    """加载配置文件"""
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    log.debug(f"配置文件加载成功: {config_path}")
    return config or {}


def parse_cookie_string(cookie_str: str) -> dict[str, str]:
    """
    解析形如 "a=b; c=d" 的 Cookie 字符串为 dict.
    """
    cookie_str = (cookie_str or "").strip()
    if not cookie_str:
        return {}

    jar: dict[str, str] = {}
    try:
        c = SimpleCookie()
        c.load(cookie_str)
        for k, morsel in c.items():
            jar[k] = morsel.value
        if jar:
            return jar
    except Exception:
        pass

    # 兜底：简单 split
    parts = [p.strip() for p in cookie_str.split(";") if p.strip()]
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            jar[k.strip()] = v.strip()
    return jar


def to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def format_points(value: Optional[Decimal]) -> str:
    """
    将积分显示为更友好的字符串：
    - 13.0000000000000000 -> 13
    - 13.50000000 -> 13.5
    """
    if value is None:
        return "未知"
    # 先规范化去掉指数形式，再去尾随 0
    s = format(value.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


# ==================== 数据结构 ====================

@dataclass(frozen=True)
class CheckinResult:
    ok: bool
    already_checked: bool
    gained_points: Optional[Decimal]
    total_points: Optional[Decimal]
    message: str


# ==================== API 客户端 ====================

class GladosClient:
    def __init__(
        self,
        cookie: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        checkin_token: Optional[str] = None,
        timeout: int = 30,
    ):
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.base_url = (base_url or "").strip().rstrip("/")
        self.checkin_token = (checkin_token or "").strip()
        self.timeout = timeout

        if not self.base_url:
            raise ValueError("base_url 为空")

        # 这两个头在一些站点/反爬策略下会影响请求是否被接受
        self.session.headers["origin"] = self.base_url
        self.session.headers["referer"] = f"{self.base_url}/console/checkin"

        if not self.checkin_token:
            host = (urlparse(self.base_url).hostname or "").strip()
            # docs/glados.md 的 token="glados.one"，推断 token 是域名
            self.checkin_token = host or "glados.one"

        cookie = (cookie or "").strip()
        if not cookie:
            raise ValueError("Cookie 为空")

        # 既设置 Cookie 头，也尽量解析到 cookie jar，提高兼容性
        self.session.headers["cookie"] = cookie
        parsed = parse_cookie_string(cookie)
        if parsed:
            self.session.cookies.update(parsed)

    def _request_json(self, method: str, path: str, *, json_body: Optional[dict] = None, retry: int = 3) -> dict:
        url = f"{self.base_url}{path}"
        last_err: Optional[Exception] = None

        for attempt in range(1, retry + 1):
            try:
                resp = self.session.request(method, url, json=json_body, timeout=self.timeout)
                # 401/403 基本可判定 Cookie 无效，直接抛出更清晰错误
                if resp.status_code in (401, 403):
                    raise PermissionError(f"未授权(HTTP {resp.status_code})，Cookie 可能已失效")
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, dict):
                    raise ValueError(f"响应不是 JSON 对象: {data!r}")
                return data
            except PermissionError as e:
                raise e
            except Exception as e:
                last_err = e
                log.warning(f"请求失败 {method} {path} (尝试 {attempt}/{retry}): {e}")
        raise RuntimeError(f"请求失败 {method} {path}: {last_err}")

    def get_status(self) -> dict:
        return self._request_json("GET", "/api/user/status", retry=3)

    def checkin(self) -> dict:
        # docs/glados.md 给出的请求体
        return self._request_json(
            "POST",
            "/api/user/checkin",
            json_body={"token": self.checkin_token},
            retry=3,
        )


# ==================== Telegram 推送 ====================

def send_telegram(config: dict, message: str) -> bool:
    tg_config = config.get("telegram", {})
    bot_token = tg_config.get("bot_token", "")
    chat_id = tg_config.get("chat_id", "")

    if not bot_token or not chat_id:
        log.warning("Telegram 配置不完整，跳过推送")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            log.info("✅ Telegram 推送成功")
            return True
        log.warning(f"❌ Telegram 推送失败: {resp.text}")
        return False
    except Exception as e:
        log.warning(f"❌ Telegram 推送异常: {e}")
        return False


def build_success_message(email: str, result: CheckinResult) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "重复签到（今日已签到）" if result.already_checked else "签到成功"
    gain_line = (
        "💰 重复签到: 今日已领取过积分"
        if result.already_checked
        else f"💰 本次获得: +{format_points(result.gained_points)} Points"
    )
    return f"""✅ <b>GLaDOS 签到成功</b>

👤 邮箱: {email or "未知"}
📋 状态: {status}

{gain_line}
🎯 总积分: {format_points(result.total_points)} Points

⏰ {now}"""


def build_failure_message(email: Optional[str], reason: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""❌ <b>GLaDOS 签到失败</b>

👤 邮箱: {email or "未知"}
❗ 原因: {reason}

⏰ {now}"""


# ==================== 签到主逻辑 ====================

def run_checkin(config: dict) -> tuple[bool, str]:
    glados_config = config.get("glados", {}) or {}
    cookie = glados_config.get("cookie", "") or ""
    base_url = (glados_config.get("base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).strip()
    checkin_token = glados_config.get("checkin_token")
    timeout = int(glados_config.get("timeout", 30) or 30)

    if not cookie.strip():
        return False, build_failure_message(None, "未配置 glados.cookie")

    client = GladosClient(cookie=cookie, base_url=base_url, checkin_token=checkin_token, timeout=timeout)

    # 先取邮箱（同时验证 Cookie）
    status = client.get_status()
    if status.get("code") != 0:
        return False, build_failure_message(None, f"获取账户信息失败: {status}")

    data = status.get("data") or {}
    email = data.get("email", "") or ""

    # 签到
    chk = client.checkin()
    code = chk.get("code")
    points = to_decimal(chk.get("points"))

    # 从流水里推断总积分（balance 是签到后的余额）
    total_points: Optional[Decimal] = None
    lst = chk.get("list")
    if isinstance(lst, list) and lst:
        first = lst[0] if isinstance(lst[0], dict) else {}
        total_points = to_decimal(first.get("balance"))

    # code=0: 本次签到成功；code=1: 今日已签到（docs 里如此）
    if code in (0, 1):
        already_checked = code == 1
        gained_points = points if points is not None else Decimal(0)
        msg_text = str(chk.get("message") or "")
        result = CheckinResult(
            ok=True,
            already_checked=already_checked,
            gained_points=gained_points,
            total_points=total_points,
            message=msg_text,
        )
        log.info(f"邮箱: {email}")
        if already_checked:
            log.info(f"签到状态: 重复签到(今日已签到); 总积分: {format_points(total_points)}")
        else:
            log.info(
                f"签到状态: 成功; 本次获得: +{format_points(gained_points)}; 总积分: {format_points(total_points)}"
            )
        return True, build_success_message(email, result)

    # 其他 code: 认为失败
    return False, build_failure_message(email, f"签到失败: {chk}")


def main() -> None:
    log.info("=" * 50)
    log.info("========== GLaDOS 签到开始 ==========")
    log.info("=" * 50)

    try:
        config = load_config()
        success, message = run_checkin(config)
        send_telegram(config, message)
        if not success:
            sys.exit(1)
    except Exception as e:
        log.exception(f"签到异常: {e}")
        try:
            config = load_config()
            send_telegram(config, build_failure_message(None, str(e)))
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()

