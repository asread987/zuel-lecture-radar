#!/usr/bin/env bash
# =============================================================================
# ZUEL 讲座雷达 —— 一键安装
# =============================================================================
# 别人拿到这个项目后，只需要跑这一条命令：
#
#     bash scripts/setup.sh            # 用当前系统用户名当标识
#     bash scripts/setup.sh 张三        # 指定标识
#
# 它会：建虚拟环境 → 装依赖 → 建运行目录 → 环境自检 → 生成一份用户配置。
# 之后只需要改一处（自己的微信推送凭据），再跑 test-push 验证即可。
# =============================================================================
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"

echo "=============================================================="
echo " ZUEL 讲座雷达 · 安装"
echo " 项目目录：$PROJECT"
echo "=============================================================="
echo

# ── 1. 找一个可用的 Python（>=3.9）────────────────────────────────
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    ver="$("$c" -c 'import sys; print("%d%02d" % sys.version_info[:2])' 2>/dev/null || echo 0)"
    if [ "$ver" -ge 309 ]; then
        PY="$(command -v "$c")"
        echo "✓ Python：$PY（$("$c" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')）"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "✗ 没找到 Python 3.9 及以上版本。"
    echo "  macOS： brew install python@3.12"
    echo "  Ubuntu： sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

# ── 2. 虚拟环境 + 依赖 ────────────────────────────────────────────
if [ ! -x .venv/bin/python ]; then
    echo "→ 创建虚拟环境 .venv"
    "$PY" -m venv .venv
else
    echo "→ 复用已有的 .venv"
fi
echo "→ 安装依赖（requests / beautifulsoup4 / lxml / PyYAML）"
./.venv/bin/pip install -q --upgrade pip >/dev/null 2>&1 || true
if ! ./.venv/bin/pip install -q -r requirements.txt; then
    echo "✗ 依赖安装失败。若是网络问题，可换国内镜像："
    echo "  ./.venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
    exit 1
fi
echo "✓ 依赖就绪"

# ── 3. 运行目录 ──────────────────────────────────────────────────
mkdir -p data out config/users
touch config/users/.gitkeep
echo "✓ 运行目录就绪（data/ out/ config/users/）"

# ── 4. 环境自检 ──────────────────────────────────────────────────
echo
./.venv/bin/python run.py doctor || true

# ── 5. 生成第一份用户配置 ────────────────────────────────────────
echo
NAME="${1:-$(whoami)}"
CFG="config/users/$NAME.yaml"
if [ -e "$CFG" ]; then
    echo "→ 已存在用户配置 $CFG，跳过生成"
else
    ./.venv/bin/python run.py init-user "$NAME"
fi

cat <<EOF

==============================================================
 装好了。下一步只有两步：

 1) 编辑 $CFG
    把 channels 里那行「- type: console」换成你自己的推送渠道。
    推荐最省事的 Server酱：打开 https://sct.ftqq.com 扫码绑定微信，
    拿到 SendKey（形如 SCTxxxxxx），填进去：

        channels:
          - type: serverchan
            key: SCT你的SendKey

 2) 验证推送链路：

        ./.venv/bin/python run.py test-push --user $NAME

    微信收到测试消息后，先干跑一次看报告内容：

        ./.venv/bin/python run.py run --user $NAME --dry-run

 最后装上定时任务（每天 20:00 唤起，逐用户判断是否到期）：

        bash scripts/install_schedule.sh
==============================================================
EOF
