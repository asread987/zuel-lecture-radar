#!/usr/bin/env bash
# =============================================================================
# ZUEL 讲座雷达 —— 安装 / 卸载定时任务（跨平台入口）
# =============================================================================
#   bash scripts/install_schedule.sh              # 安装，每天 20:00 唤起
#   bash scripts/install_schedule.sh install 21 30   # 自定义时刻
#   bash scripts/install_schedule.sh uninstall    # 卸载
#
# 这里配的是「调度器每天唤起的时刻」，不是「每位用户的推送间隔」。
# 每位用户的间隔由各自配置里的 interval_days 决定，由 run.py due 逐用户判断。
# =============================================================================
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-install}"
HOUR="${2:-20}"
MINUTE="${3:-0}"
LABEL="com.zuel.lecture-radar"
MARKER="# zuel-lecture-radar"

if [ ! -x "$PROJECT/.venv/bin/python" ]; then
    echo "✗ 还没装依赖。先跑： bash scripts/setup.sh" >&2
    exit 1
fi
PYTHON="$PROJECT/.venv/bin/python"

case "$(uname -s)" in

# ── macOS：launchd ───────────────────────────────────────────────
Darwin)
    if [ "$ACTION" = "uninstall" ]; then
        bash "$PROJECT/scripts/install_launchd.sh" --uninstall
    else
        bash "$PROJECT/scripts/install_launchd.sh" "$HOUR" "$MINUTE"
    fi
    ;;

# ── Linux：crontab ──────────────────────────────────────────────
Linux)
    if ! command -v crontab >/dev/null 2>&1; then
        echo "✗ 没找到 crontab。装一下 cron：sudo apt install cron && sudo service cron start" >&2
        exit 1
    fi

    TMP="$(mktemp)"
    crontab -l 2>/dev/null | grep -v "$MARKER" > "$TMP" || true

    if [ "$ACTION" = "uninstall" ]; then
        crontab "$TMP" 2>/dev/null || true
        rm -f "$TMP"
        echo "✓ 已卸载定时任务（$MARKER）"
        exit 0
    fi

    # 每小时的指定分钟执行，但只在目标小时动作 —— 用 cron 的 hour 字段直接表达
    echo "$MINUTE $HOUR * * * cd $PROJECT && $PYTHON run.py due >> $PROJECT/data/cron.log 2>&1 $MARKER" >> "$TMP"
    crontab "$TMP"
    rm -f "$TMP"
    echo "✓ 已安装 cron 任务：每天 ${HOUR}:$(printf '%02d' "$MINUTE") 执行 run.py due"
    echo
    echo "常用操作："
    echo "  crontab -l                                   # 查看"
    echo "  tail -f '$PROJECT/data/cron.log'             # 查看运行日志"
    echo "  bash scripts/install_schedule.sh uninstall   # 卸载"
    ;;

# ── 其它（含 Windows）───────────────────────────────────────────
*)
    cat <<EOF
当前系统（$(uname -s)）没有自动安装脚本，请手动配置计划任务。

Windows（任务计划程序 / PowerShell）—— 每天 20:00 执行一次：

  程序：   $PYTHON
  参数：   run.py due
  起始于： $PROJECT

对应的命令行写法（管理员 PowerShell，执行一次）：

  \$a = New-ScheduledTaskAction -Execute "$PYTHON" -Argument "run.py due" -WorkingDirectory "$PROJECT"
  \$t = New-ScheduledTaskTrigger -Daily -At 20:00
  Register-ScheduledTask -TaskName "ZUEL-Lecture-Radar" -Action \$a -Trigger \$t

验证：先手动跑一次 $PYTHON run.py doctor
EOF
    ;;
esac
