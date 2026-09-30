#!/usr/bin/env bash
# 安装 / 卸载 ZUEL 讲座雷达的定时任务（macOS launchd）
#
#   bash scripts/install_launchd.sh              # 安装，默认每天 20:00 唤起
#   bash scripts/install_launchd.sh 21 30        # 自定义唤起时刻（21:30）
#   bash scripts/install_launchd.sh --uninstall  # 卸载
#
# 注意：这里配置的是「调度器每天唤起的时刻」，不是「每位用户的推送间隔」。
# 每位用户的间隔天数由其配置里的 interval_days 决定，由 run.py due 逐用户判断。
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.zuel.lecture-radar"
PLIST_SRC="$PROJECT/scripts/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"

if [[ "${1:-}" == "--uninstall" ]]; then
    launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || launchctl unload "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "已卸载定时任务 $LABEL"
    exit 0
fi

HOUR="${1:-20}"
MINUTE="${2:-0}"

# ── 选择 Python 解释器：优先找已装好依赖的那个 ─────────────────────
# 注意：不要依赖 $USER（cron / 非登录 shell 里可能未定义），统一用 $HOME。
pick_python() {
    local cands=(
        "$PROJECT/.venv/bin/python"
        "$HOME/.workbuddy/binaries/python/envs/default/bin/python"
        "$(command -v python3 || true)"
        "/opt/homebrew/bin/python3"
        "/usr/bin/python3"
    )
    for p in "${cands[@]}"; do
        [[ -n "$p" && -x "$p" ]] || continue
        if "$p" -c "import requests, bs4, yaml" >/dev/null 2>&1; then
            echo "$p"; return 0
        fi
    done
    return 1
}

PYTHON="$(pick_python || true)"
if [[ -z "$PYTHON" ]]; then
    echo "✗ 没有找到已安装依赖的 Python 解释器。" >&2
    echo "  请先执行： cd '$PROJECT' && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi
echo "✓ 使用解释器：$PYTHON"

mkdir -p "$PROJECT/data" "$HOME/Library/LaunchAgents"

sed -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__PROJECT__|$PROJECT|g" \
    -e "s|__HOUR__|$HOUR|g" \
    -e "s|__MINUTE__|$MINUTE|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# 重新加载
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST_DST"

echo "✓ 已安装定时任务：$PLIST_DST"
echo "  每天 ${HOUR}:$(printf '%02d' "$MINUTE") 唤起调度器，逐用户判断是否到期并推送"
echo
echo "常用操作："
echo "  launchctl print gui/$UID_NUM/$LABEL | head -30   # 查看状态"
echo "  launchctl kickstart -k gui/$UID_NUM/$LABEL       # 立即手动触发一次"
echo "  tail -f '$PROJECT/data/launchd.out.log'          # 查看运行日志"
echo "  bash scripts/install_launchd.sh --uninstall      # 卸载"
