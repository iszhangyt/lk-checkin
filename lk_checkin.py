#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
轻之国度（LK）APP 自动签到脚本

功能：
- 自动完成每日签到任务
- 支持 security_key 直接配置或账号密码登录
- 签到完成后推送结果到 Telegram
"""

import base64
import json
import logging
import random
import sys
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests
import yaml

# ==================== 日志配置 ====================

# 设置 stdout 编码为 UTF-8，解决 Windows 控制台的 Unicode 问题
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)


# ==================== 常量定义 ====================

BASE_URL = "https://api.lightnovel.fun"
HEADERS = {
    "User-Agent": "Dart/3.8 (dart:io)",
    "Content-Type": "application/json; charset=UTF-8",
    "Accept-Encoding": "gzip",
    # "Host": "api.lightnovel.fun",  # requests 库会自动根据 URL 填充 Host 头部，通常无需手动指定
}

# 任务信息映射
TASK_INFO = {
    8: "登录签到",
    1: "阅读帖子",
    2: "收藏帖子",
    3: "点赞帖子",
    5: "分享帖子",
    6: "投币帖子",
    7: "全部完成"
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
    return config


def decode_response(data: Any) -> Any:
    """
    解码 API 响应
    当 gz=1 时，响应是 base64 编码的 zlib 压缩内容
    """
    if isinstance(data, str):
        try:
            # Base64 解码
            compressed = base64.b64decode(data)
            # Zlib 解压
            decompressed = zlib.decompress(compressed)
            # JSON 解析
            return json.loads(decompressed)
        except Exception as e:
            log.warning(f"响应解码失败: {e}")
            return data
    return data


def build_request_body(security_key: str, extra_data: Optional[dict] = None) -> dict:
    """构建请求体"""
    body = {
        "platform": "android",
        "client": "app",
        "sign": "",
        "ver_name": "0.11.53",
        "ver_code": 193,
        "d": {
            "security_key": security_key
        },
        "gz": 1
    }
    if extra_data:
        body["d"].update(extra_data)
    return body


# ==================== 缓存管理 ====================

CACHE_FILE = Path(__file__).parent / ".lk_cache.json"


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


def get_cached_security_key(username: str) -> Optional[str]:
    """获取缓存的 security_key"""
    cache = load_cache()
    return cache.get(username, {}).get("security_key")


def cache_security_key(username: str, security_key: str) -> None:
    """缓存 security_key"""
    cache = load_cache()
    cache[username] = {
        "security_key": security_key,
        "cached_at": datetime.now().isoformat()
    }
    save_cache(cache)


def login(username: str, password: str) -> Optional[str]:
    """
    使用账号密码登录，获取 security_key
    返回: security_key 或 None（登录失败）
    """
    log.info(f"登录中: {username}")
    
    url = f"{BASE_URL}/api/user/login"
    body = {
        "platform": "android",
        "client": "app",
        "sign": "",
        "ver_name": "0.11.53",
        "ver_code": 193,
        "is_encrypted": 0,
        "d": {
            "username": username,
            "password": password
        },
        "gz": 1
    }
    
    try:
        resp = requests.post(url, json=body, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        
        # 解码响应
        raw_content = resp.content
        try:
            compressed = base64.b64decode(raw_content)
            decompressed = zlib.decompress(compressed)
            result = json.loads(decompressed)
        except Exception:
            result = resp.json()
        
        if result.get("code") == 0:
            data = result["data"]
            security_key = data.get("security_key", "")
            uid = data.get("uid", "")
            log.info(f"登录成功: uid={uid}")
            return security_key
        else:
            log.error(f"登录失败: {result}")
            return None
            
    except Exception as e:
        log.error(f"登录异常: {e}")
        return None


# ==================== API 请求封装 ====================

class LKClient:
    """轻之国度 API 客户端"""
    
    def __init__(self, security_key: str):
        self.security_key = security_key
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        
        # 用户信息
        self.uid: Optional[int] = None
        self.nickname: Optional[str] = None
        self.coin: int = 0
        self.exp: int = 0
    
    def _post(self, path: str, extra_data: Optional[dict] = None, retry: int = 3) -> Optional[dict]:
        """发送 POST 请求"""
        url = f"{BASE_URL}{path}"
        body = build_request_body(self.security_key, extra_data)
        
        for attempt in range(retry):
            try:
                log.debug(f"请求: POST {path}")
                log.debug(f"请求体: {json.dumps(body, ensure_ascii=False)}")
                
                resp = self.session.post(url, json=body, timeout=30)
                resp.raise_for_status()
                
                # 当 gz=1 时，整个响应体是 base64 编码的 zlib 压缩内容
                raw_content = resp.content
                
                # 尝试解码整个响应体
                try:
                    # base64 解码 -> zlib 解压 -> JSON 解析
                    compressed = base64.b64decode(raw_content)
                    decompressed = zlib.decompress(compressed)
                    result = json.loads(decompressed)
                    log.debug(f"响应: code={result.get('code')}")
                except Exception as decode_err:
                    log.debug(f"整体解码失败: {decode_err}")
                    # 如果解码失败，尝试直接按 JSON 解析（可能是 gz=0 的情况）
                    try:
                        result = resp.json()
                        # 如果 data 字段是字符串，可能需要单独解码
                        if "data" in result and isinstance(result["data"], str):
                            result["data"] = decode_response(result["data"])
                        log.debug(f"直接解析响应: code={result.get('code')}")
                    except Exception as json_err:
                        log.warning(f"JSON解析也失败: {json_err}")
                        raise json_err
                
                return result
                
            except requests.RequestException as e:
                log.warning(f"请求失败 (尝试 {attempt + 1}/{retry}): {e}")
                if attempt == retry - 1:
                    return None
            except Exception as e:
                log.warning(f"请求处理失败 (尝试 {attempt + 1}/{retry}): {e}")
                if attempt == retry - 1:
                    return None
        
        return None
    
    def get_user_info(self) -> bool:
        """获取用户信息"""
        log.info("获取用户信息...")
        
        # 从 security_key 解析 uid
        parts = self.security_key.split(":")
        if len(parts) >= 2:
            uid = int(parts[1])
        else:
            log.error("无法从 security_key 解析 uid")
            return False
        
        result = self._post("/api/user/info", {"uid": uid})
        if not result or result.get("code") != 0:
            log.error(f"获取用户信息失败: {result}")
            return False
        
        data = result["data"]
        self.uid = data["uid"]
        self.nickname = data["nickname"]
        self.coin = data["balance"]["coin"]
        self.exp = data["level"]["exp"]
        
        log.info(f"用户: uid={self.uid}, 昵称={self.nickname}")
        log.info(f"当前: 轻币={self.coin}, 经验={self.exp}")
        return True
    
    def get_task_list(self) -> Optional[dict]:
        """获取任务列表"""
        log.info("获取任务列表...")
        
        result = self._post("/api/task/list")
        if not result or result.get("code") != 0:
            log.error(f"获取任务列表失败: {result}")
            return None
        
        data = result["data"]
        
        # 解析任务状态
        log.info("任务状态:")
        for item in data.get("items", []):
            task_id = item["id"]
            status = item["status"]
            status_text = {0: "未完成", 1: "待领取", 2: "已领取"}.get(status, f"未知({status})")
            log.info(f"  任务{task_id} {TASK_INFO.get(task_id, '未知')}: {status_text}")
        
        return data
    
    def get_article_list(self, page: int = 1, page_size: int = 40) -> Optional[list]:
        """获取文章列表"""
        log.info(f"获取文章列表: 第{page}页")
        
        result = self._post("/api/category/get-article-by-cate", {
            "parent_gid": 3,
            "gid": 106,
            "page": page,
            "pageSize": page_size
        })
        
        if not result or result.get("code") != 0:
            log.error(f"获取文章列表失败: {result}")
            return None
        
        articles = result["data"]["list"]
        log.debug(f"获取到 {len(articles)} 篇文章")
        return articles
    
    def get_article_detail(self, aid: int) -> Optional[dict]:
        """获取文章详情"""
        log.debug(f"获取文章详情: aid={aid}")
        
        result = self._post("/api/article/get-detail", {
            "aid": aid,
            "simple": 1
        })
        
        if not result or result.get("code") != 0:
            log.debug(f"获取文章详情失败: aid={aid}")
            return None
        
        return result["data"]
    
    def find_valid_article(self, max_pages: int = 5) -> Optional[int]:
        """
        查找可用的文章（未点赞、未收藏、未投币）
        
        策略：先获取多页文章，然后随机选取检查，避免总是选择固定的文章
        """
        log.info(f"搜索可用文章（获取 {max_pages} 页）...")
        
        # 步骤1: 获取所有页的文章列表
        all_articles = []
        for page in range(1, max_pages + 1):
            articles = self.get_article_list(page)
            if articles:
                all_articles.extend(articles)
        
        if not all_articles:
            log.error("获取文章列表失败")
            return None
        
        log.info(f"共获取 {len(all_articles)} 篇文章，开始随机检查...")
        
        # 步骤2: 打乱顺序，随机检查
        random.shuffle(all_articles)
        
        for article in all_articles:
            aid = article["aid"]
            detail = self.get_article_detail(aid)
            if not detail:
                continue
            
            already_like = detail.get("already_like", 1)
            already_fav = detail.get("already_fav", 1)
            already_coin = detail.get("already_coin", 1)
            
            log.debug(f"文章 aid={aid}: like={already_like}, fav={already_fav}, coin={already_coin}")
            
            # 检查是否可用
            if already_like == 0 and already_fav == 0 and already_coin == 0:
                log.info(f"✓ 找到可用文章: aid={aid}, 标题={detail.get('title', '')[:30]}")
                return aid
        
        log.error(f"在 {len(all_articles)} 篇文章中未找到可用文章")
        return None
    
    def add_history(self, aid: int) -> bool:
        """添加历史记录（阅读任务前置动作）"""
        log.info(f"添加历史记录: aid={aid}")
        
        result = self._post("/api/history/add-history", {
            "fid": aid,
            "class": 1
        })
        
        success = result and result.get("code") == 0
        log.info(f"添加历史记录: {'✅ 成功' if success else '❌ 失败'}")
        return success
    
    def add_collection(self, aid: int) -> bool:
        """收藏文章（收藏任务前置动作）"""
        log.info(f"收藏文章: aid={aid}")
        
        result = self._post("/api/history/add-collection", {
            "fid": aid,
            "class": 1
        })
        
        success = result and result.get("code") == 0
        log.info(f"收藏文章: {'✅ 成功' if success else '❌ 失败'}")
        return success
    
    def del_collection(self, aid: int) -> bool:
        """取消收藏"""
        log.info(f"取消收藏: aid={aid}")
        
        result = self._post("/api/history/del-collection", {
            "fid": aid,
            "class": 1
        })
        
        success = result and result.get("code") == 0
        log.info(f"取消收藏: {'✅ 成功' if success else '❌ 失败'}")
        return success
    
    def like_article(self, aid: int) -> bool:
        """点赞文章（点赞任务前置动作）"""
        log.info(f"点赞文章: aid={aid}")
        
        result = self._post("/api/article/like", {"aid": aid})
        
        success = result and result.get("code") == 0
        log.info(f"点赞文章: {'✅ 成功' if success else '❌ 失败'}")
        return success
    
    def use_coin(self, aid: int, number: int = 10) -> bool:
        """投币（投币任务前置动作）"""
        log.info(f"投币: aid={aid}, 数量={number}")
        
        result = self._post("/api/coin/use", {
            "goods_id": 2,
            "params": aid,
            "price": 1,
            "number": number,
            "total_price": number
        })
        
        success = result and result.get("code") == 0
        log.info(f"投币: {'✅ 成功' if success else '❌ 失败'}")
        return success
    
    def claim_reward(self, task_id: int) -> Optional[dict]:
        """领取任务奖励"""
        log.info(f"领取奖励: 任务{task_id} {TASK_INFO.get(task_id, '')}")
        
        result = self._post("/api/task/complete", {"id": task_id})
        
        if result and result.get("code") == 0:
            data = result.get("data", {})
            coin = data.get("coin", 0)
            exp = data.get("exp", 0)
            log.info(f"✅ 领取成功 (+{coin}轻币, +{exp}经验)")
            return {"coin": coin, "exp": exp}
        else:
            log.warning(f"❌ 领取失败: {result}")
            return None


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


def build_success_message(nickname: str, task_results: list, total_coin: int, total_exp: int, 
                          final_coin: int, final_exp: int) -> str:
    """
    构建成功推送消息
    task_results: [(task_id, coin, exp, status_code), ...]
        - status_code: "new" 本次领取, "done" 已完成, "skip" 跳过, "fail" 失败
    """
    task_lines = []
    
    # 按任务 ID 排序显示
    task_order = [8, 1, 2, 3, 5, 6, 7]
    task_dict = {r[0]: r for r in task_results}
    
    for task_id in task_order:
        task_name = TASK_INFO.get(task_id, f"任务{task_id}")
        if task_id in task_dict:
            _, coin, exp, status_code = task_dict[task_id]
            if status_code == "new":
                task_lines.append(f"• {task_name}: +{coin}轻币, +{exp}经验")
            elif status_code == "done":
                task_lines.append(f"• {task_name}: 已完成")
            elif status_code == "skip":
                task_lines.append(f"• {task_name}: 跳过")
            elif status_code == "fail":
                task_lines.append(f"• {task_name}: 失败")
    
    task_detail = "\n".join(task_lines) if task_lines else "• 无任务记录"
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    return f"""✅ <b>轻之国度签到成功</b>

👤 用户: {nickname}

📋 任务详情:
{task_detail}

💰 总计: +{total_coin}轻币, +{total_exp}经验
📈 当前: 轻币{final_coin}, 经验{final_exp}

⏰ {now}"""


def build_failure_message(nickname: str, reason: str) -> str:
    """构建失败推送消息"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    return f"""❌ <b>轻之国度签到失败</b>

👤 用户: {nickname or "未知"}
❗ 原因: {reason}

⏰ {now}"""


# ==================== 签到主逻辑 ====================

def do_checkin(config: dict) -> tuple[bool, str]:
    """
    执行签到
    返回: (是否成功, 推送消息)
    """
    lk_config = config.get("lk", {})
    security_key = lk_config.get("security_key", "")
    username = lk_config.get("username", "")
    password = lk_config.get("password", "")
    
    # 认证逻辑：
    # 1. 优先使用配置的 security_key
    # 2. 其次使用缓存的 security_key（针对账号密码方式）
    # 3. 最后登录获取新的 security_key
    
    if security_key:
        log.info("认证方式: security_key（配置）")
    elif username and password:
        # 尝试使用缓存的 security_key
        cached_key = get_cached_security_key(username)
        if cached_key:
            log.info("认证方式: security_key（缓存）")
            security_key = cached_key
        else:
            log.info("认证方式: 账号密码登录")
            security_key = login(username, password)
            if security_key:
                cache_security_key(username, security_key)
            else:
                return False, build_failure_message(None, "登录失败")
    else:
        log.error("未配置 security_key 或账号密码")
        return False, build_failure_message(None, "未配置认证信息")
    
    # 创建客户端
    client = LKClient(security_key)
    
    # 获取用户信息（验证 key 是否有效）
    if not client.get_user_info():
        # key 可能已失效，尝试重新登录
        if username and password:
            log.warning("security_key 可能已失效，尝试重新登录...")
            security_key = login(username, password)
            if security_key:
                cache_security_key(username, security_key)
                client = LKClient(security_key)
                if not client.get_user_info():
                    return False, build_failure_message(None, "获取用户信息失败")
            else:
                return False, build_failure_message(None, "重新登录失败")
        else:
            return False, build_failure_message(None, "获取用户信息失败")
    
    # 记录签到前状态
    coin_before = client.coin
    exp_before = client.exp
    log.info(f"签到前: 轻币={coin_before}, 经验={exp_before}")
    
    # 获取任务列表
    task_data = client.get_task_list()
    if not task_data:
        return False, build_failure_message(client.nickname, "获取任务列表失败")
    
    # 解析任务状态
    task_status = {}
    for item in task_data.get("items", []):
        task_status[item["id"]] = item["status"]
    
    # 查找可用文章
    aid = client.find_valid_article()
    if not aid:
        return False, build_failure_message(client.nickname, "找不到可用文章")
    
    # 记录任务结果: (task_id, coin, exp, status_code)
    # status_code: "new" 本次领取, "done" 已完成, "skip" 跳过, "fail" 失败
    task_results = []
    
    # ========== 执行任务 ==========
    
    # 任务8: 登录签到
    log.info("=" * 40)
    log.info("=== 任务8: 登录签到 ===")
    reward = client.claim_reward(8)
    if reward:
        task_results.append((8, reward["coin"], reward["exp"], "new"))
    else:
        task_results.append((8, 0, 0, "done"))
    
    # 任务1: 阅读帖子
    log.info("=" * 40)
    status = task_status.get(1, 2)
    log.info(f"=== 任务1: 阅读帖子 (status={status}) ===")
    if status < 2:
        if status == 0:
            client.add_history(aid)
        reward = client.claim_reward(1)
        if reward:
            task_results.append((1, reward["coin"], reward["exp"], "new"))
    else:
        log.info("已完成，跳过")
        task_results.append((1, 0, 0, "done"))
    
    # 任务2: 收藏帖子
    log.info("=" * 40)
    status = task_status.get(2, 2)
    log.info(f"=== 任务2: 收藏帖子 (status={status}) ===")
    collected = False
    if status < 2:
        if status == 0:
            collected = client.add_collection(aid)
        reward = client.claim_reward(2)
        if reward:
            task_results.append((2, reward["coin"], reward["exp"], "new"))
    else:
        log.info("已完成，跳过")
        task_results.append((2, 0, 0, "done"))
    
    # 任务3: 点赞帖子
    log.info("=" * 40)
    status = task_status.get(3, 2)
    log.info(f"=== 任务3: 点赞帖子 (status={status}) ===")
    if status < 2:
        if status == 0:
            client.like_article(aid)
        reward = client.claim_reward(3)
        if reward:
            task_results.append((3, reward["coin"], reward["exp"], "new"))
    else:
        log.info("已完成，跳过")
        task_results.append((3, 0, 0, "done"))
    
    # 任务5: 分享帖子
    log.info("=" * 40)
    status = task_status.get(5, 2)
    log.info(f"=== 任务5: 分享帖子 (status={status}) ===")
    if status < 2:
        reward = client.claim_reward(5)
        if reward:
            task_results.append((5, reward["coin"], reward["exp"], "new"))
    else:
        log.info("已完成，跳过")
        task_results.append((5, 0, 0, "done"))
    
    # 任务6: 投币帖子
    log.info("=" * 40)
    status = task_status.get(6, 2)
    log.info(f"=== 任务6: 投币帖子 (status={status}) ===")
    if status < 2:
        # 刷新用户信息，获取最新余额
        client.get_user_info()
        log.info(f"当前余额: {client.coin} 轻币")
        
        # 检查余额
        if client.coin >= 10:
            if status == 0:
                client.use_coin(aid, 10)
            reward = client.claim_reward(6)
            if reward:
                task_results.append((6, reward["coin"], reward["exp"], "new"))
            else:
                task_results.append((6, 0, 0, "fail"))
        else:
            log.warning(f"余额不足 ({client.coin} < 10)，跳过投币任务")
            task_results.append((6, 0, 0, "skip"))
    else:
        log.info("已完成，跳过")
        task_results.append((6, 0, 0, "done"))
    
    # 任务7: 全部完成
    log.info("=" * 40)
    # 重新获取任务状态
    task_data = client.get_task_list()
    main_status = task_data.get("status", 2) if task_data else 2
    log.info(f"=== 任务7: 全部完成 (status={main_status}) ===")
    if main_status < 2:
        reward = client.claim_reward(7)
        if reward:
            task_results.append((7, reward["coin"], reward["exp"], "new"))
        else:
            # 领取失败（可能是前置任务未完成）
            task_results.append((7, 0, 0, "fail"))
    else:
        log.info("已完成，跳过")
        task_results.append((7, 0, 0, "done"))
    
    # ========== 清理 ==========
    log.info("=" * 40)
    log.info("=== 清理 ===")
    if collected:
        client.del_collection(aid)
    else:
        log.info("未收藏文章，无需取消")
    
    # ========== 统计结果 ==========
    log.info("=" * 40)
    
    # 刷新用户信息
    client.get_user_info()
    coin_after = client.coin
    exp_after = client.exp
    
    # 只统计本次领取的奖励
    total_coin = sum(r[1] for r in task_results if r[3] == "new")
    total_exp = sum(r[2] for r in task_results if r[3] == "new")
    
    log.info("========== 签到完成 ==========")
    log.info(f"签到后: 轻币={coin_after}, 经验={exp_after}")
    log.info(f"本次获得: +{total_coin}轻币, +{total_exp}经验")
    
    # 构建推送消息
    message = build_success_message(
        client.nickname,
        task_results,
        total_coin,
        total_exp,
        coin_after,
        exp_after
    )
    
    return True, message


def main():
    """主函数"""
    log.info("=" * 50)
    log.info("========== 轻之国度签到开始 ==========")
    log.info("=" * 50)
    
    try:
        # 加载配置
        config = load_config()
        
        # 执行签到
        success, message = do_checkin(config)
        
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
