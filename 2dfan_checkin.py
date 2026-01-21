#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2DFan 自动签到脚本

功能：
- 自动完成每日签到任务
- 支持账号密码登录
- 签到完成后推送结果到 Telegram
"""

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
import yaml


# ==================== 日志配置 ====================

# 设置 stdout 编码为 UTF-8，解决 Windows 控制台的 Unicode 问题
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)


# ==================== 常量定义 ====================

BASE_URL = "https://api.acghost.vip"
HEADERS = {
    "User-Agent": "Dart/2.12 (dart:io)",
    "Accept-Language": "zh-cn",
    "Accept-Encoding": "gzip",
    "Platform": "android",
    "Token": "app2dfan_test",
    "Referer": "https://api.galge.fun/",
}


# ==================== 缓存管理 ====================

CACHE_FILE = Path(__file__).parent / ".2dfan_cache.json"


def load_cache() -> dict:
    """加载缓存文件"""
    if not CACHE_FILE.exists():
        return {}
    
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"加载缓存失败: {e}")
        return {}


def save_cache(cache: dict) -> bool:
    """保存缓存文件"""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        log.debug(f"缓存已保存: {CACHE_FILE}")
        return True
    except Exception as e:
        log.warning(f"保存缓存失败: {e}")
        return False


# ==================== 工具函数 ====================

def load_config() -> dict:
    """加载配置文件"""
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    log.debug(f"配置文件加载成功: {config_path}")
    return config


# ==================== API 客户端 ====================

class TwodfanClient:
    """2DFan API 客户端"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        
        self.access_token: Optional[str] = None
        self.access_token_expired_at: Optional[int] = None
        self.user_id: Optional[int] = None
        self.username: Optional[str] = None
        self.avatar_url: Optional[str] = None
        
        # 用户统计信息
        self.points: int = 0
        self.checkins_count: int = 0
        self.serial_checkins: int = 0
        self.checkin_rank: int = 0
    
    
    def load_session_from_cache(self, data: dict) -> None:
        """从缓存加载会话信息 (仅加载 Cookie 和用户信息)"""
        self.user_id = data.get("uid")
        self.username = data.get("username")
        
        # 恢复 Cookie
        cookies = data.get("cookies", {})
        self.session.cookies.update(cookies)
            
        log.info(f"已加载缓存 Cookie: user={self.username}")

    def to_cache_data(self) -> dict:
        """导出需要缓存的会话信息 (只缓存 Cookie)"""
        return {
            "uid": self.user_id,
            "username": self.username,
            "cookies": self.session.cookies.get_dict(),
            "updated_at": int(time.time())
        }

    def get_access_token(self) -> bool:
        """获取 access-token"""
        log.info("获取 access-token...")
        
        url = f"{BASE_URL}/api/static/token"
        
        try:
            resp = self.session.post(url, timeout=30)
            resp.raise_for_status()
            
            data = resp.json()
            self.access_token = data.get("token")
            # API 返回的是字符串时间戳 "1768946450"
            expired_at_str = data.get("expired_at")
            self.access_token_expired_at = int(expired_at_str) if expired_at_str else None
            
            if self.access_token:
                log.info(f"✅ 获取 access-token 成功，过期时间: {datetime.fromtimestamp(self.access_token_expired_at) if self.access_token_expired_at else '未知'}")
                self.session.headers["Access-Token"] = self.access_token
                return True
            else:
                log.error(f"获取 access-token 失败: {data}")
                return False
                
        except Exception as e:
            log.error(f"获取 access-token 异常: {e}")
            return False
    
    def login(self, username: str, password: str) -> bool:
        """
        使用账号密码登录
        成功后会设置 session cookie
        """
        log.info(f"登录中: {username}")
        
        url = f"{BASE_URL}/api/users/sign_in"
        payload = {
            "login": username,
            "password": password
        }
        
        try:
            resp = self.session.post(
                url, 
                json=payload, 
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=30
            )
            resp.raise_for_status()
            
            data = resp.json()
            
            if "id" in data:
                self.user_id = data["id"]
                self.username = data.get("name", "")
                self.avatar_url = data.get("avatar_url", "")
                log.info(f"✅ 登录成功: uid={self.user_id}, name={self.username}")
                return True
            else:
                log.error(f"登录失败: {data}")
                return False
                
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                log.error("登录失败: 用户名或密码错误")
            else:
                log.error(f"登录失败: HTTP {e.response.status_code}")
            return False
        except Exception as e:
            log.error(f"登录异常: {e}")
            return False
    
    def get_user_info(self) -> bool:
        """获取用户详细信息"""
        if not self.user_id:
            # 防御性检查：确保已获取 uid，防止 URL 拼接错误
            log.error("未登录(无 uid)，无法获取用户信息")
            return False
        
        log.info("获取用户信息...")
        
        # 注意：API 路径中有双斜杠
        url = f"{BASE_URL}/api/users//{self.user_id}"
        
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            
            data = resp.json()
            
            self.points = data.get("points", 0)
            self.checkins_count = data.get("checkins_count", 0)
            self.serial_checkins = data.get("serial_checkins", 0)
            self.checkin_rank = data.get("checkin_rank", 0)
            
            log.info(f"用户: {self.username}")
            log.info(f"积分: {self.points}, 签到次数: {self.checkins_count}, 连续签到: {self.serial_checkins}天")
            return True
            
        except Exception as e:
            # 如果是 401/403，说明 Cookie 失效
            log.warning(f"获取用户信息失败 (可能是 Cookie 失效): {e}")
            return False
    
    def do_checkin(self) -> dict:
        """
        执行签到
        返回: {"success": bool, "points": int, "serial_checkins": int, "checkins_count": int, "already_checked": bool}
        """
        log.info("执行签到...")
        
        url = f"{BASE_URL}/api/checkins"
        
        try:
            resp = self.session.post(url, timeout=30)
            resp.raise_for_status()
            
            data = resp.json()
            
            points = data.get("points", 0)
            serial_checkins = data.get("serial_checkins", 0)
            checkins_count = data.get("checkins_count", 0)
            
            # 判断是否已签到：如果返回的 points=0 且 checkins_count=0 则表示今日已签到
            if points == 0 and checkins_count == 0 and serial_checkins == 0:
                log.info("今日已签到")
                return {
                    "success": True,
                    "points": 0,
                    "serial_checkins": self.serial_checkins,
                    "checkins_count": self.checkins_count,
                    "already_checked": True
                }
            else:
                log.info(f"✅ 签到成功: +{points}积分, 连续签到{serial_checkins}天, 累计签到{checkins_count}次")
                return {
                    "success": True,
                    "points": points,
                    "serial_checkins": serial_checkins,
                    "checkins_count": checkins_count,
                    "already_checked": False
                }
                
        except Exception as e:
            log.error(f"签到异常: {e}")
            return {
                "success": False,
                "points": 0,
                "serial_checkins": 0,
                "checkins_count": 0,
                "already_checked": False
            }


# ==================== Telegram 推送 ====================

def send_telegram(config: dict, message: str) -> bool:
    """发送 Telegram 消息"""
    tg_config = config.get("telegram", {})
    bot_token = tg_config.get("bot_token", "")
    chat_id = tg_config.get("chat_id", "")
    
    if not bot_token or not chat_id:
        log.warning("Telegram 配置不完整，跳过推送")
        return False
    
    log.info("发送 Telegram 通知...")
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            log.info("✅ Telegram 推送成功")
            return True
        else:
            log.warning(f"❌ Telegram 推送失败: {resp.text}")
            return False
    except Exception as e:
        log.warning(f"❌ Telegram 推送异常: {e}")
        return False


def build_success_message(username: str, result: dict, user_points: int) -> str:
    """构建成功推送消息"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if result.get("already_checked"):
        status = "今日已签到"
        points_text = "+0积分"
    else:
        status = "签到成功"
        points_text = f"+{result['points']}积分"
    
    return f"""✅ <b>2DFan 签到成功</b>

👤 用户: {username}
📋 状态: {status}

💰 获得: {points_text}
📊 连续签到: {result['serial_checkins']}天
📈 累计签到: {result['checkins_count']}次
🎯 当前积分: {user_points}

⏰ {now}"""


def build_failure_message(username: Optional[str], reason: str) -> str:
    """构建失败推送消息"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    return f"""❌ <b>2DFan 签到失败</b>

👤 用户: {username or "未知"}
❗ 原因: {reason}

⏰ {now}"""





# ==================== 签到主逻辑 ====================

def run_checkin(config: dict) -> tuple[bool, str]:
    """
    执行签到
    返回: (是否成功, 推送消息)
    """
    twodfan_config = config.get("2dfan", {})
    username = twodfan_config.get("username", "")
    password = twodfan_config.get("password", "")
    
    if not username or not password:
        log.error("未配置 2dfan 账号密码")
        return False, build_failure_message(None, "未配置账号密码")
    
    # 创建客户端
    client = TwodfanClient()
    
    # 步骤1: 总是获取最新的 access-token
    if not client.get_access_token():
        return False, build_failure_message(None, "获取 access-token 失败")

    # 尝试加载缓存 Cookie
    cache = load_cache()
    user_cache = cache.get(username)
    
    login_success = False
    
    if user_cache:
        log.info("发现缓存 Cookie，尝试复用...")
        client.load_session_from_cache(user_cache)
        
        # 验证 Cookie 是否有效 (尝试获取用户信息)
        if client.get_user_info():
            log.info("✅ Cookie 有效，跳过账号登录")
            login_success = True
        else:
            log.warning("缓存 Cookie 已失效")
    
    # 如果缓存无效或不存在，执行账号密码登录
    if not login_success:
        log.info("使用账号密码登录...")
        
        # 步骤2: 登录
        if not client.login(username, password):
            return False, build_failure_message(username, "登录失败")
            
        # 登录成功，保存缓存 (只保存 Cookie 和用户信息)
        cache[username] = client.to_cache_data()
        save_cache(cache)
    
    # 此时应该已经登录成功
    
    if login_success:
        # 复用缓存时，points 已经在验证时更新了
        points_before = client.points
    else:
        # 新登录时，需要获取一次用户信息
        if not client.get_user_info():
             return False, build_failure_message(username, "登录后获取用户信息失败")
        points_before = client.points
    

    
    # 步骤4: 执行签到
    result = client.do_checkin()
    
    if not result["success"]:
        return False, build_failure_message(client.username, "签到请求失败")
    
    # 步骤5: 获取最新用户信息
    client.get_user_info()
    
    # 更新结果中的统计信息（如果是已签到状态，使用用户信息中的值）
    if result["already_checked"]:
        result["serial_checkins"] = client.serial_checkins
        result["checkins_count"] = client.checkins_count
    
    # 构建推送消息
    message = build_success_message(client.username, result, client.points)
    
    log.info("=" * 40)
    log.info("========== 签到完成 ==========")
    log.info(f"签到前积分: {points_before}")
    log.info(f"签到后积分: {client.points}")
    log.info(f"本次获得: +{result['points']}积分")
    
    return True, message


def main():
    """主函数"""
    log.info("=" * 50)
    log.info("========== 2DFan 签到开始 ==========")
    log.info("=" * 50)
    
    try:
        # 加载配置
        config = load_config()
        
        # 执行签到
        success, message = run_checkin(config)
        
        # 推送结果
        send_telegram(config, message)
        
        if success:
            log.info("签到流程完成")
        else:
            log.error("签到失败")
            sys.exit(1)
            
    except Exception as e:
        log.exception(f"签到异常: {e}")
        
        # 尝试推送错误
        try:
            config = load_config()
            message = build_failure_message(None, str(e))
            send_telegram(config, message)
        except:
            pass
        
        sys.exit(1)


if __name__ == "__main__":
    main()
