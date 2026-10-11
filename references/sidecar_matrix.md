# 🛡️ AGENTKIT SIDECAR CHECKLISTS & SKEPTIC ATTACK MATRIX

> **Sứ mệnh:** Cung cấp ma trận kiểm soát chất lượng chuyên sâu cho Code, Review, Architecture và Debate.

---

## 1. CODE SIDECAR (KHI VIẾT/SỬA CODE)
**Trigger:** `write_to_file`, `replace_file_content`, `/code`

| # | Tiêu chí | Câu hỏi kiểm tra bắt buộc |
|---|---|---|
| 1 | **Blast Radius** | Hàm/file này được bao nhiêu nơi gọi? Sửa có gãy caller? |
| 2 | **Null-Safety** | Input từ bên ngoài (API, user, DB) đã check null/undefined? |
| 3 | **Timeout** | Mọi I/O call có timeout? Không treo vô hạn? |
| 4 | **N+1 Query** | Có vòng lặp query DB? $\rightarrow$ Gộp batch |
| 5 | **Resource Leak** | File handle, connection, stream đóng đúng? |
| 6 | **Error Handling** | Lỗi được bắt, log rõ, không nuốt âm thầm? |
| 7 | **Self-Adversarial** | Code này có thể sai ở đâu? Edge-case nào chưa cover? |
| 8 | **AST Syntax Gate** | Cú pháp có hợp lệ 100%? Không có unclosed brackets (`scripts/code_guard.py`) |
| 9 | **Snapshot Safety** | Tự động tạo snapshot `.bak` trước khi sửa code lớn trong L1 Self-Heal để hoàn tác an toàn |

---

## 2. REVIEW SIDECAR (KHI REVIEW CODE / PULL REQUEST)
**Trigger:** `/review`, `--ultra review`, `--ultra review-pr`

| # | Tiêu chí | Câu hỏi kiểm tra bắt buộc |
|---|---|---|
| 1 | **False Positive** | Nhận xét review có chính xác? Có đang hiểu sai context? |
| 2 | **Missing Bug** | Có bug nào đã bỏ sót chưa? Đặc biệt: race-condition, null deref, off-by-one |
| 3 | **Security Surface** | Thay đổi này mở thêm attack surface nào? Input chưa validate? |
| 4 | **Breaking Change** | Có phá backward compatibility? Caller nào bị ảnh hưởng? |
| 5 | **Test Coverage** | Thay đổi có test đi kèm? Cần thêm test case nào? |

---

## 3. ARCHITECTURE SIDECAR (KHI THIẾT KẾ / LẬP PLAN)
**Trigger:** `/plan`, `/design`, `/advise`, `--ultra plan`

| # | Tiêu chí | Câu hỏi kiểm tra bắt buộc |
|---|---|---|
| 1 | **Scalability** | Giải pháp có scale được 10x/100x? Bottleneck ở đâu? |
| 2 | **Migration Path** | Hệ thống cũ chuyển sang mới thế nào? Downtime bao lâu? |
| 3 | **Rollback Plan** | Nếu deploy xong phát hiện bug, rollback mất bao lâu? Mất data? |
| 4 | **Cost Projection** | Chi phí ẩn? Infra/token/storage tăng bao nhiêu? |
| 5 | **Dependency Risk** | Phụ thuộc bên thứ 3 nào? Nếu họ ngừng hoạt động thì sao? |

---

## 4. SKEPTIC ATTACK CHECKLIST (CHO TRANH BIỆN `--debate`)
**BẮT BUỘC Skeptic phải đánh giá từng điểm:** ✅ Safe / ⚠️ Risk / ❌ Critical

| # | Góc tấn công | Câu hỏi Skeptic phải trả lời |
|---|---|---|
| 1 | **Availability** | Nếu service/dependency X chết, hệ thống còn hoạt động? Có single point of failure? |
| 2 | **Data Integrity** | Nếu 2 request đến cùng lúc (race condition), data có bị corrupt/mất? |
| 3 | **Scale** | Nếu lượng dữ liệu/traffic tăng 100x, giải pháp chịu nổi? Bottleneck ở đâu? |
| 4 | **Rollback** | Deploy xong phát hiện bug $\rightarrow$ rollback mất bao lâu? Có mất data? |
| 5 | **Hidden Cost** | Chi phí ẩn: token/API call/storage tăng bao nhiêu? Debt kỹ thuật? |
| 6 | **Migration** | Hệ thống cũ chuyển sang mới thế nào? Downtime? Backward compat? |
| 7 | **Security Surface** | Thêm bao nhiêu attack surface mới? Input nào chưa validate? |

---

## 5. DEBUG SIDECAR (KHI SỬA LỖI)
**Trigger:** `/debug`, Error Recovery L1/L2/L3, bug report

| # | Tiêu chí | Câu hỏi kiểm tra bắt buộc |
|---|---|---|
| 1 | **Root Cause vs Symptom** | Đang sửa nguyên nhân gốc hay chỉ vá triệu chứng bề mặt? |
| 2 | **Reproduction** | Có bước tái hiện lỗi rõ ràng? Log evidence đầy đủ? |
| 3 | **Regression Check** | Bản sửa có gây lỗi mới ở nơi khác? Caller nào bị ảnh hưởng? |
| 4 | **Guard Placement** | Guard/validation đặt ở hàm dùng chung (Ponytail) hay vá từng caller riêng lẻ? |
| 5 | **Test Coverage** | Có regression test đi kèm bản sửa? Edge-case nào cần test thêm? |
| 6 | **Rollback Safety** | Nếu bản sửa gây side-effect, rollback nhanh không? CoW snapshot đã tạo? |

---

## 6. DEPLOY SIDECAR (KHI TRIỂN KHAI)
**Trigger:** `/deploy`, `/rollback`, CI/CD pipeline

| # | Tiêu chí | Câu hỏi kiểm tra bắt buộc |
|---|---|---|
| 1 | **Pre-Deploy Health** | Build thành công? Tests pass? Lint clean? |
| 2 | **Environment Diff** | Config staging vs production có khác biệt gì? Env vars đầy đủ? |
| 3 | **Rollback Plan** | Rollback script sẵn sàng? Thời gian rollback ước tính? Có mất data? |
| 4 | **Breaking Changes** | API version bump? Client cũ có tương thích? Migration script cần chạy? |
| 5 | **Monitoring** | Alert/dashboard đã cấu hình cho metrics mới? Log level phù hợp? |
| 6 | **Canary/Gradual** | Deploy toàn bộ hay canary 5% trước? Feature flag cần bật/tắt? |

---

## 7. CROSS-WORKER CONTRACT SIDECAR (KHI MERGE CODE TỪ NHIỀU WORKERS — 🆕 v5.3)
**Trigger:** Sau khi N-Way Merge từ 2+ parallel workers, trước Gate 0 (SDE Contract)

| # | Tiêu chí | Câu hỏi kiểm tra bắt buộc |
|---|---|---|
| 1 | **Dangling Import** | Worker A import symbol từ Worker B mà Worker B chưa tạo? |
| 2 | **Signature Mismatch** | Hàm được gọi với đúng số params và kiểu trả về? |
| 3 | **API Contract** | Endpoint request/response schema Worker 1 export có khớp với Worker 2 consume? |
| 4 | **Shared State** | 2 workers có sửa cùng 1 global variable, config, hoặc DB table? Race condition? |
| 5 | **Naming Collision** | 2 workers có tạo function/class trùng tên nhưng logic khác nhau? |
| 6 | **Missing Integration** | Worker 3 (test) đã cover được interface giữa Worker 1 và Worker 2? |

