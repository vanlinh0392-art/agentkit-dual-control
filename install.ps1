# =====================================================================
# AgentKit Dual-Control — 1-Click Installer for Windows PowerShell
# =====================================================================
[CmdletBinding()]
param(
    [string]$TargetDir = "$env:USERPROFILE\.gemini\config\skills\agentkit-dual-control",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   🛡️ AGENTKIT DUAL-CONTROL v5.5.0 — 1-CLICK INSTALLER      " -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Check Git
Write-Host "`n[1/6] Kiểm tra Git..." -NoNewline
if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
    Write-Host " [FAILED]" -ForegroundColor Red
    Write-Error "Yêu cầu cài đặt Git trước khi tiếp tục!"
}
Write-Host " [OK]" -ForegroundColor Green

# 2. Check Python
Write-Host "[2/6] Kiểm tra Python 3.10+..." -NoNewline
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $pythonCmd = $cmd
        break
    }
}
if (-not $pythonCmd) {
    Write-Host " [FAILED]" -ForegroundColor Red
    Write-Error "Yêu cầu cài đặt Python 3.10+ trước khi tiếp tục!"
}
Write-Host " [OK] (Dùng: $pythonCmd)" -ForegroundColor Green

# 3. Setup Target Directory
Write-Host "[3/6] Triển khai mã nguồn vào: $TargetDir..."
$parentDir = Split-Path $TargetDir -Parent
if (-not (Test-Path $parentDir)) {
    New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
}

$repoUrl = "https://github.com/vanlinh0392-art/agentkit-dual-control.git"

if (Test-Path "$TargetDir\.git") {
    Write-Host "  • Đã phát hiện repo, đang đồng bộ phiên bản mới nhất..." -ForegroundColor Yellow
    Push-Location $TargetDir
    try {
        git pull origin main
    } finally {
        Pop-Location
    }
} elseif (Test-Path $TargetDir) {
    Write-Host "  • Thư mục đã tồn tại nhưng chưa có git. Sao lưu và clone mới..." -ForegroundColor Yellow
    $backupDir = "$TargetDir.bak_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Move-Item -Path $TargetDir -Destination $backupDir -Force
    git clone $repoUrl $TargetDir
} else {
    Write-Host "  • Đang clone mã nguồn từ GitHub..." -ForegroundColor Cyan
    git clone $repoUrl $TargetDir
}

# 4. Check Synergistic Ecosystem Tools (RTK, CodeGraph)
Write-Host "`n[4/6] Kiểm tra các công cụ hệ sinh thái phụ thuộc..."
if (Get-Command "rtk" -ErrorAction SilentlyContinue) {
    Write-Host "  ✅ RTK (Rust Token Killer): Đã cài đặt" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ RTK: Chưa phát hiện trên PATH (Khuyên dùng: cài đặt RTK để tiết kiệm 70% token)" -ForegroundColor Yellow
}

if (Get-Command "codegraph" -ErrorAction SilentlyContinue) {
    Write-Host "  ✅ CodeGraph: Đã cài đặt (Semantic AST Graph sẵn sàng)" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ CodeGraph: Chưa phát hiện (Cài đặt nhanh: npm i -g @colbymchenry/codegraph)" -ForegroundColor Yellow
}

# 5. Configure GEMINI.md Rules (Doctrines Fix Cứng)
Write-Host "`n[5/6] Đồng bộ các Rule & Doctrine fix cứng vào GEMINI.md..."
$geminiDir = "$env:USERPROFILE\.gemini"
$userGeminiMd = "$geminiDir\GEMINI.md"
$srcGeminiMd = "$TargetDir\GEMINI.md"

if (Test-Path $srcGeminiMd) {
    if (-not (Test-Path $userGeminiMd)) {
        if (-not (Test-Path $geminiDir)) {
            New-Item -ItemType Directory -Path $geminiDir -Force | Out-Null
        }
        Copy-Item -Path $srcGeminiMd -Destination $userGeminiMd -Force
        Write-Host "  ✅ Đã khởi tạo mới ~/.gemini/GEMINI.md với đầy đủ các Doctrine cốt lõi." -ForegroundColor Green
    } else {
        $existingContent = Get-Content -Path $userGeminiMd -Raw -Encoding UTF8
        if ($existingContent -match "AGENTKIT DUAL-CONTROL DOCTRINE") {
            Write-Host "  ✅ ~/.gemini/GEMINI.md đã có sẵn các Doctrine của AgentKit Dual-Control." -ForegroundColor Green
        } else {
            # Backup bản cũ
            $backupMd = "$userGeminiMd.bak_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
            Copy-Item -Path $userGeminiMd -Destination $backupMd -Force
            # Đọc phần doctrine từ nguồn
            $srcContent = Get-Content -Path $srcGeminiMd -Raw -Encoding UTF8
            Add-Content -Path $userGeminiMd -Value "`n`n$srcContent" -Encoding UTF8
            Write-Host "  ✅ Đã tự động cập nhật các Rule fix cứng vào ~/.gemini/GEMINI.md (đã backup tại: $backupMd)." -ForegroundColor Green
        }
    }
} else {
    Write-Host "  ⚠️ Không tìm thấy GEMINI.md trong thư mục mã nguồn skill." -ForegroundColor Yellow
}

# 6. Run Health Check
Write-Host "`n[6/6] Kiểm chứng hệ thống (Health Check)..." -ForegroundColor Cyan
Push-Location $TargetDir
try {
    & $pythonCmd "scripts\health_check.py"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n============================================================" -ForegroundColor Green
        Write-Host "  🎉 CÀI ĐẶT THÀNH CÔNG AGENTKIT DUAL-CONTROL v5.5.0!        " -ForegroundColor Green
        Write-Host "============================================================" -ForegroundColor Green
        Write-Host "• Vị trí cài đặt: $TargetDir" -ForegroundColor Gray
        Write-Host "• Rule fix cứng: $userGeminiMd" -ForegroundColor Gray
        Write-Host "• Trạng thái: Sẵn sàng hoạt động trên Antigravity & Gemini CLI." -ForegroundColor Cyan
    } else {
        Write-Host "`n⚠️ Cảnh báo: Health check có một số lưu ý. Vui lòng kiểm tra lại." -ForegroundColor Yellow
    }
} finally {
    Pop-Location
}
