#!/usr/bin/env bash
# =====================================================================
# AgentKit Dual-Control — 1-Click Installer for Linux & macOS
# =====================================================================
set -e

TARGET_DIR="${HOME}/.gemini/config/skills/agentkit-dual-control"
REPO_URL="https://github.com/vanlinh0392-art/agentkit-dual-control.git"

echo "============================================================"
echo "   🛡️ AGENTKIT DUAL-CONTROL v5.5.0 — 1-CLICK INSTALLER      "
echo "============================================================"

# 1. Check Git
if ! command -v git &> /dev/null; then
    echo "❌ Lỗi: Yêu cầu cài đặt Git trước khi tiếp tục!"
    exit 1
fi

# 2. Check Python
PYTHON_CMD=""
for cmd in python3 python py; do
    if command -v $cmd &> /dev/null; then
        PYTHON_CMD=$cmd
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "❌ Lỗi: Yêu cầu cài đặt Python 3.10+ trước khi tiếp tục!"
    exit 1
fi

# 3. Setup Target Directory
mkdir -p "$(dirname "$TARGET_DIR")"
if [ -d "$TARGET_DIR/.git" ]; then
    echo "• Đang đồng bộ phiên bản mới nhất từ GitHub..."
    cd "$TARGET_DIR" && git pull origin main
elif [ -d "$TARGET_DIR" ]; then
    BACKUP_DIR="${TARGET_DIR}.bak_$(date +%Y%m%d_%H%M%S)"
    echo "• Thư mục đã tồn tại nhưng chưa có git. Di chuyển sang ${BACKUP_DIR}..."
    mv "$TARGET_DIR" "$BACKUP_DIR"
    git clone "$REPO_URL" "$TARGET_DIR"
else
    echo "• Đang clone mã nguồn từ GitHub..."
    git clone "$REPO_URL" "$TARGET_DIR"
fi

# 4. Check Synergistic Ecosystem Tools
echo ""
echo "[4/5] Kiểm tra công cụ phụ trợ..."
if command -v rtk &> /dev/null; then
    echo "  ✅ RTK (Rust Token Killer): Đã cài đặt"
else
    echo "  ⚠️ RTK: Chưa phát hiện (Khuyên dùng cài đặt để tối ưu token)"
fi

if command -v codegraph &> /dev/null; then
    echo "  ✅ CodeGraph: Đã cài đặt"
else
    echo "  ⚠️ CodeGraph: Chưa phát hiện (Cài đặt: npm i -g @colbymchenry/codegraph)"
fi

# 5. Run Health Check
echo ""
echo "[5/5] Kiểm chứng hệ thống (Health Check)..."
cd "$TARGET_DIR"
$PYTHON_CMD scripts/health_check.py

echo ""
echo "============================================================"
echo "  🎉 CÀI ĐẶT THÀNH CÔNG AGENTKIT DUAL-CONTROL v5.5.0!        "
echo "============================================================"
echo "• Vị trí cài đặt: $TARGET_DIR"
echo "• Trạng thái: Sẵn sàng hoạt động."
