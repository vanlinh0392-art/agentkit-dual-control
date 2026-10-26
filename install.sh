#!/usr/bin/env bash
# =====================================================================
# AgentKit Dual-Control v6.0.0 — 1-Click Installer for Linux & macOS
# Tối ưu 100% cho Google Antigravity 2.0 & Omni-Skills Architecture
# =====================================================================
set -e

TARGET_DIR="${HOME}/.gemini/config/skills/agentkit-dual-control"
REPO_URL="https://github.com/vanlinh0392-art/agentkit-dual-control.git"
GEMINI_DIR="${HOME}/.gemini"
USER_GEMINI_MD="${GEMINI_DIR}/GEMINI.md"
PLUGINS_TARGET_DIR="${GEMINI_DIR}/config/plugins"

echo "============================================================"
echo "   🛡️ AGENTKIT DUAL-CONTROL v6.0.0 — ANTIGRAVITY 2.0 SETUP   "
echo "      12 Plugins | Omni-Skills Registry | Zero-Drift         "
echo "============================================================"

# 1. Check Git
echo ""
echo "[1/7] Kiểm tra Git..."
if ! command -v git &> /dev/null; then
    echo "❌ Lỗi: Yêu cầu cài đặt Git trước khi tiếp tục!"
    exit 1
fi
echo "  ✅ Git sẵn sàng"

# 2. Check Python
echo "[2/7] Kiểm tra Python 3.10+..."
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
echo "  ✅ Python sẵn sàng (Dùng: $PYTHON_CMD)"

# 3. Setup Target Directory
echo "[3/7] Triển khai mã nguồn vào: $TARGET_DIR..."
mkdir -p "$(dirname "$TARGET_DIR")"
if [ -d "$TARGET_DIR/.git" ]; then
    echo "  • Đang đồng bộ phiên bản mới nhất từ GitHub..."
    cd "$TARGET_DIR" && git pull origin main || echo "  ⚠️ Không thể pull từ remote, tiếp tục với mã nguồn hiện tại."
elif [ -d "$TARGET_DIR" ]; then
    echo "  • Thư mục đã tồn tại cục bộ. Sử dụng mã nguồn hiện tại."
else
    echo "  • Đang clone mã nguồn từ GitHub..."
    git clone "$REPO_URL" "$TARGET_DIR"
fi

# 4. Check Synergistic Ecosystem Tools
echo ""
echo "[4/7] Kiểm tra các công cụ hệ sinh thái Antigravity 2.0..."
if command -v rtk &> /dev/null; then
    echo "  ✅ RTK (Rust Token Killer): Đã cài đặt (Nén token terminal 70-90%)"
else
    echo "  ⚠️ RTK: Chưa phát hiện (Khuyên dùng: cài đặt RTK để tối ưu token)"
fi

if command -v codegraph &> /dev/null; then
    echo "  ✅ CodeGraph: Đã cài đặt (Semantic AST Graph sẵn sàng)"
else
    echo "  ⚠️ CodeGraph: Chưa phát hiện (Cài đặt: npm i -g @colbymchenry/codegraph)"
fi

# 5. Deploy 12 Plugins to ~/.gemini/config/plugins/
echo ""
echo "[5/7] Triển khai 12 Plugins cho Google Antigravity 2.0..."
mkdir -p "$PLUGINS_TARGET_DIR"
BUNDLED_PLUGINS_DIR="${TARGET_DIR}/plugins"
if [ -d "$BUNDLED_PLUGINS_DIR" ]; then
    cp -r "${BUNDLED_PLUGINS_DIR}"/* "$PLUGINS_TARGET_DIR"/
    echo "  ✅ Đã đồng bộ thành công 12 plugins vào $PLUGINS_TARGET_DIR"
else
    echo "  ⚠️ Thư mục plugins bundled chưa có trong $TARGET_DIR"
fi

# 6. Configure GEMINI.md Rules
echo ""
echo "[6/7] Đồng bộ các Rule & Doctrine fix cứng vào GEMINI.md..."
mkdir -p "$GEMINI_DIR"
SRC_GEMINI_MD="${TARGET_DIR}/GEMINI.md"

if [ -f "$SRC_GEMINI_MD" ]; then
    if [ ! -f "$USER_GEMINI_MD" ]; then
        cp "$SRC_GEMINI_MD" "$USER_GEMINI_MD"
        echo "  ✅ Đã khởi tạo mới ~/.gemini/GEMINI.md với đầy đủ các Doctrine cốt lõi."
    else
        if grep -q "AGENTKIT DUAL-CONTROL DOCTRINE" "$USER_GEMINI_MD"; then
            echo "  ✅ ~/.gemini/GEMINI.md đã có sẵn các Doctrine của AgentKit Dual-Control."
        else
            BACKUP_MD="${USER_GEMINI_MD}.bak_$(date +%Y%m%d_%H%M%S)"
            cp "$USER_GEMINI_MD" "$BACKUP_MD"
            cat "$SRC_GEMINI_MD" >> "$USER_GEMINI_MD"
            echo "  ✅ Đã tự động cập nhật các Rule fix cứng vào ~/.gemini/GEMINI.md (đã backup: $BACKUP_MD)."
        fi
    fi
else
    echo "  ⚠️ Không tìm thấy GEMINI.md trong thư mục mã nguồn skill."
fi

# 7. Verify Registry & Health Check
echo ""
echo "[7/7] Kiểm tra Omni-Skills Registry & Chạy Health Check..."
cd "$TARGET_DIR"
if [ -f "registry/skills_index.json" ]; then
    echo "  ✅ skills_index.json: Sẵn sàng (Inverted Intent Router)"
fi
if [ -d "registry/storage" ]; then
    STORAGE_COUNT=$(ls -1 registry/storage | wc -l)
    echo "  ✅ Standalone Storage: $STORAGE_COUNT skills lưu trữ"
fi

$PYTHON_CMD scripts/health_check.py

echo ""
echo "============================================================"
echo "  🎉 CÀI ĐẶT THÀNH CÔNG AGENTKIT DUAL-CONTROL v6.0.0!        "
echo "============================================================"
echo "• Vị trí cài đặt: $TARGET_DIR"
echo "• Plugins: $PLUGINS_TARGET_DIR (12 plugins)"
echo "• Rule fix cứng: $USER_GEMINI_MD"
echo "• Trạng thái: 100% Sẵn sàng trên Antigravity 2.0 & Gemini CLI."
