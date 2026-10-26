# =====================================================================
# AgentKit Dual-Control v6.0.0 — 1-Click Installer for Windows PowerShell
# Tối ưu 100% cho Google Antigravity 2.0 & Omni-Skills Architecture
# =====================================================================
[CmdletBinding()]
param(
    [string]$TargetDir = "$env:USERPROFILE\.gemini\config\skills\agentkit-dual-control",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   🛡️ AGENTKIT DUAL-CONTROL v6.0.0 — ANTIGRAVITY 2.0 SETUP   " -ForegroundColor Yellow
Write-Host "      12 Plugins | Omni-Skills Registry | Zero-Drift         " -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Check Git
Write-Host "`n[1/7] Kiểm tra Git..." -NoNewline
if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
    Write-Host " [FAILED]" -ForegroundColor Red
    Write-Error "Yêu cầu cài đặt Git trước khi tiếp tục!"
}
Write-Host " [OK]" -ForegroundColor Green

# 2. Check Python
Write-Host "[2/7] Kiểm tra Python 3.10+..." -NoNewline
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
Write-Host "[3/7] Triển khai mã nguồn vào: $TargetDir..."
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
    } catch {
        Write-Host "  ⚠️ Không thể pull từ remote (offline hoặc local branch), giữ nguyên local files." -ForegroundColor Gray
    } finally {
        Pop-Location
    }
} elseif (Test-Path $TargetDir) {
    Write-Host "  • Thư mục đã tồn tại cục bộ. Sử dụng mã nguồn hiện tại." -ForegroundColor Green
} else {
    Write-Host "  • Đang clone mã nguồn từ GitHub..." -ForegroundColor Cyan
    git clone $repoUrl $TargetDir
}

# 4. Check Synergistic Ecosystem Tools (RTK, CodeGraph, Antigravity)
Write-Host "`n[4/7] Kiểm tra các công cụ hệ sinh thái Google Antigravity 2.0..."
if (Get-Command "rtk" -ErrorAction SilentlyContinue) {
    Write-Host "  ✅ RTK (Rust Token Killer): Đã cài đặt (Nén token terminal 70-90%)" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ RTK: Chưa phát hiện trên PATH (Khuyên dùng: cài đặt RTK để nén token)" -ForegroundColor Yellow
}

if (Get-Command "codegraph" -ErrorAction SilentlyContinue) {
    Write-Host "  ✅ CodeGraph: Đã cài đặt (Semantic AST Knowledge Graph sẵn sàng)" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ CodeGraph: Chưa phát hiện (Cài đặt: npm i -g @colbymchenry/codegraph)" -ForegroundColor Yellow
}

$geminiHome = "$env:USERPROFILE\.gemini"
if (Test-Path $geminiHome) {
    Write-Host "  ✅ Thư mục Google Antigravity & Gemini: $geminiHome" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ Chưa thấy ~/.gemini, tạo mới tự động." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $geminiHome -Force | Out-Null
}

# 5. Deploy & Link 12 Plugins to ~/.gemini/config/plugins/
Write-Host "`n[5/7] Triển khai 12 Plugins cho Google Antigravity 2.0..." -ForegroundColor Cyan
$pluginsTargetDir = "$geminiHome\config\plugins"
if (-not (Test-Path $pluginsTargetDir)) {
    New-Item -ItemType Directory -Path $pluginsTargetDir -Force | Out-Null
}

$bundledPluginsDir = "$TargetDir\plugins"
if (Test-Path $bundledPluginsDir) {
    $plugins = Get-ChildItem -Path $bundledPluginsDir -Directory
    $pCount = 0
    foreach ($p in $plugins) {
        $dest = Join-Path $pluginsTargetDir $p.Name
        Copy-Item -Path $p.FullName -Destination $dest -Recurse -Force
        $pCount++
    }
    Write-Host "  ✅ Đã đồng bộ thành công $pCount/12 plugins vào $pluginsTargetDir" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ Thư mục plugins bundled chưa có trong $TargetDir" -ForegroundColor Yellow
}

# 6. Configure GEMINI.md Rules (Doctrines Fix Cứng)
Write-Host "`n[6/7] Đồng bộ các Rule & Doctrine fix cứng vào GEMINI.md..." -ForegroundColor Cyan
$userGeminiMd = "$geminiHome\GEMINI.md"
$srcGeminiMd = "$TargetDir\GEMINI.md"

if (Test-Path $srcGeminiMd) {
    if (-not (Test-Path $userGeminiMd)) {
        Copy-Item -Path $srcGeminiMd -Destination $userGeminiMd -Force
        Write-Host "  ✅ Đã khởi tạo mới ~/.gemini/GEMINI.md với đầy đủ các Doctrine cốt lõi." -ForegroundColor Green
    } else {
        $existingContent = Get-Content -Path $userGeminiMd -Raw -Encoding UTF8
        if ($existingContent -match "AGENTKIT DUAL-CONTROL DOCTRINE") {
            Write-Host "  ✅ ~/.gemini/GEMINI.md đã có sẵn các Doctrine của AgentKit Dual-Control." -ForegroundColor Green
        } else {
            $backupMd = "$userGeminiMd.bak_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
            Copy-Item -Path $userGeminiMd -Destination $backupMd -Force
            $srcContent = Get-Content -Path $srcGeminiMd -Raw -Encoding UTF8
            Add-Content -Path $userGeminiMd -Value "`n`n$srcContent" -Encoding UTF8
            Write-Host "  ✅ Đã tự động cập nhật các Rule fix cứng vào ~/.gemini/GEMINI.md (đã backup: $backupMd)." -ForegroundColor Green
        }
    }
} else {
    Write-Host "  ⚠️ Không tìm thấy GEMINI.md trong thư mục mã nguồn skill." -ForegroundColor Yellow
}

# 7. Verify Registry & Run Health Check
Write-Host "`n[7/7] Kiểm tra Omni-Skills Registry & Chạy Health Check..." -ForegroundColor Cyan
$skillsIndex = "$TargetDir\registry\skills_index.json"
$storageDir = "$TargetDir\registry\storage"

if (Test-Path $skillsIndex) {
    Write-Host "  ✅ skills_index.json: Sẵn sàng (Inverted Intent Router)" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ skills_index.json: Chưa tạo (Sẽ tự động khởi tạo khi load)" -ForegroundColor Yellow
}

if (Test-Path $storageDir) {
    $storageCount = (Get-ChildItem -Path $storageDir).Count
    Write-Host "  ✅ Standalone Storage: $storageCount skills lưu trữ" -ForegroundColor Green
}

Push-Location $TargetDir
try {
    & $pythonCmd "scripts\health_check.py"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n============================================================" -ForegroundColor Green
        Write-Host "  🎉 CÀI ĐẶT THÀNH CÔNG AGENTKIT DUAL-CONTROL v6.0.0!        " -ForegroundColor Green
        Write-Host "============================================================" -ForegroundColor Green
        Write-Host "• Vị trí cài đặt: $TargetDir" -ForegroundColor Gray
        Write-Host "• Plugins: $pluginsTargetDir (12 plugins)" -ForegroundColor Gray
        Write-Host "• Rule fix cứng: $userGeminiMd" -ForegroundColor Gray
        Write-Host "• Trạng thái: 100% Sẵn sàng trên Antigravity 2.0 & Gemini CLI." -ForegroundColor Cyan
    } else {
        Write-Host "`n⚠️ Cảnh báo: Health check có một số lưu ý. Vui lòng kiểm tra lại." -ForegroundColor Yellow
    }
} finally {
    Pop-Location
}
