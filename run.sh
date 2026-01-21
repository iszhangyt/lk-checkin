#!/bin/bash

# 获取脚本所在目录的绝对路径，实现通用化（不再固定 /opt/lk-checkin）
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$BASE_DIR/logs"
LOG_FILE="$LOG_DIR/checkin-$(date +%F).log"

# 创建日志目录
mkdir -p "$LOG_DIR"

# 进入工作目录
cd "$BASE_DIR" || exit 1

# 随机延迟逻辑 (0-300秒)
delay=$((RANDOM % 300))
echo "[$(date '+%F %T')] cron已触发，随机延迟: ${delay}s" >> "$LOG_FILE"
sleep "$delay"

# 定义 Python 解释器路径 (强制使用 .venv)
PYTHON_EXEC="$BASE_DIR/.venv/bin/python"
if [ ! -f "$PYTHON_EXEC" ]; then
    echo "[$(date '+%F %T')] 错误：未找到虚拟环境 $PYTHON_EXEC" >> "$LOG_FILE"
    exit 1
fi

# 1. 执行 LK 签到
echo "[$(date '+%F %T')] 启动 lk_checkin.py" >> "$LOG_FILE"
"$PYTHON_EXEC" "lk_checkin.py" >> "$LOG_FILE" 2>&1

# 2. 执行 2DFan 签到
echo "[$(date '+%F %T')] 启动 2dfan_checkin.py" >> "$LOG_FILE"
"$PYTHON_EXEC" "2dfan_checkin.py" >> "$LOG_FILE" 2>&1

echo "[$(date '+%F %T')] 所有任务完成" >> "$LOG_FILE"