# Codex & Antigravity Session Manager

Ứng dụng pywebview dùng Microsoft Edge WebView2 với giao diện kính mờ macOS Pastel để quản lý nhiều phiên đăng nhập
Codex và Antigravity trên cùng một tài khoản Windows. Hai provider được tách riêng;
Rainmeter Quota AI vẫn chỉ sử dụng Codex để giữ tương thích hiện tại.

Giao diện web nằm trong `web/` và giao tiếp bất đồng bộ với backend Python qua `window.pywebview.api`.
Trên Windows, ứng dụng ép dùng backend `edgechromium` của WebView2.
Các cột bảng dùng badge màu riêng cho gói, trạng thái, quota SESSION và quota
7 ngày; thanh quota hiển thị tỷ lệ token còn lại và tự chuyển xanh, vàng hoặc
đỏ theo phần trăm đã sử dụng. Khi quota còn 0, kể cả quota 7 ngày, thanh luôn đỏ.
Giao diện tự đọc lại trạng thái mỗi 5 phút khi cửa sổ đang hiển thị;
khi quay lại cửa sổ, dữ liệu cũng được làm mới ngay.
Skin Rainmeter Quota AI có thể gọi `main.py --switch-email EMAIL` để chuyển nhanh
phiên. Lệnh này bỏ qua an toàn nếu EMAIL đang được dùng; với phiên khác, nó luôn
backup phiên hiện tại trước khi restore rồi mở lại Codex.

## Giao diện

- Thanh bên chọn Codex hoặc Antigravity; bảng và dữ liệu đặt trên nền gần trắng dễ đọc.
- Dùng “Lưu phiên hiện tại” để sao lưu; chọn một hàng rồi “Khôi phục phiên” để chuyển tài khoản.
- Ô tìm kiếm lọc theo tên hoặc email. Có thể chọn hàng bằng Enter/Space và di chuyển bằng phím mũi tên.
- Các thao tác đăng xuất, xóa và khôi phục vẫn yêu cầu xác nhận như trước.
- Hỗ trợ bố cục hẹp, bàn phím và tùy chọn giảm chuyển động của hệ thống.
- Giao diện 1.1 được dựng lại toàn bộ: kính pastel trong suốt, dải tổng quan gọn, chuyển tab có nền trượt, hộp xác nhận riêng và chỉ một thông báo tại một thời điểm.
- Tự làm mới mỗi 5 phút; quay lại cửa sổ chỉ làm mới khi chu kỳ đã đến hạn.

## Bản portable cho Windows

Giải nén toàn bộ gói `CodexSessionManager-1.1.0-offline-windows-x64.zip` vào một thư mục có quyền ghi, rồi mở `CodexSessionManager.exe`. Không tách EXE khỏi `_internal` hoặc `WebView2Runtime`.

- Gói **offline** kèm Python và WebView2 Fixed Runtime, không cần môi trường Python dùng chung hoặc cài WebView2 riêng. “Offline” chỉ nói về cài đặt; lấy quota trực tuyến vẫn cần Internet.
- Gói **lite** không kèm WebView2, phù hợp khi máy đã có Microsoft Edge WebView2 Runtime.
- Hỗ trợ Windows 10/11 x64 có .NET Framework phù hợp cho pythonnet/WinForms. Gói portable không thay thế Codex và Antigravity trên máy.
- Nếu Antigravity đang chạy bằng quyền quản trị, hãy mở Session Manager bằng **Run as administrator** khi cần thao tác với ứng dụng đó.
- Khi chạy EXE, `tool/backups` được tạo cạnh EXE. Token và backup cá nhân không có trong bản phát hành; dữ liệu DPAPI gắn với tài khoản Windows đã tạo.
- Cấu hình OAuth riêng, nếu cần cho làm mới phiên Antigravity, lấy từ biến môi trường `AGY_OAUTH_CLIENT_SECRET` hoặc `config.local.json` theo mẫu `config.example.json`. Không commit hoặc gửi tệp cấu hình riêng cho người khác. Thiếu cấu hình này không ngăn mở giao diện; chức năng OAuth phụ thuộc cấu hình có thể không thực hiện được.

### Chạy từ mã nguồn trên máy khác

Cài Python 3.12 x64 và Microsoft Edge WebView2 Runtime, rồi mở `start-standalone.cmd`. Launcher này tự tạo môi trường `.venv` riêng trong dự án, không dùng `F:\PythonEnv`.

Đóng gói: xem [hướng dẫn build portable](scripts/PORTABLE_BUILD.md). Workflow GitHub tại `.github/workflows/build-windows.yml` tạo artifact ZIP khi chạy thủ công hoặc đẩy tag `v*`; không tự xuất bản GitHub Release.

Kiểm tra bản đóng gói mà không đọc phiên hoặc kết nối mạng:

```powershell
.\CodexSessionManager.exe --self-check --report "$env:TEMP\session-manager-check.json"
```

## Chạy ứng dụng

Dùng `start-standalone.cmd` để tạo môi trường riêng khi chạy từ mã nguồn. Bản mã nguồn GitHub cũng có `start.cmd` trỏ tới launcher độc lập này. Launcher `start.cmd` cũ trên máy phát triển vẫn được giữ để không ảnh hưởng cách chạy hiện có.

## Dữ liệu

- Nguồn phiên hiện tại: `%USERPROFILE%\.codex\auth.json`
- Backup mặc định: `tool\backups` ngay trong thư mục ứng dụng.
- Phiên Antigravity hiện tại: Generic Credential `gemini:antigravity` cùng file
  `%APPDATA%\Antigravity\credential-user-scope`.
- Backup Antigravity: `tool\backups\antigravity`.
- Có thể đổi thư mục dữ liệu bằng biến môi trường
  `CODEX_SESSION_MANAGER_DATA_DIR`. Khi chạy lần đầu với cấu hình mới, app sao
  chép các file `.csm` cũ từ `%LOCALAPPDATA%\CodexSessionManager\backups` sang
  `tool\backups`; bản cũ được giữ nguyên và file đã có không bị ghi đè.
- Quota được lấy trực tiếp bằng access token và account ID trong từng phiên đã
  lưu qua endpoint chỉ đọc `https://chatgpt.com/backend-api/wham/usage`.
- Nếu access token hết hạn, app gọi `codex app-server` với đúng phiên đó để Codex
  tự refresh, sau đó mã hóa lại phiên mới bằng Windows DPAPI.
- Kết quả được cache trong RAM 5 phút để chu kỳ cập nhật giao diện 5 phút không
  tạo request lặp. Nút **Đồng bộ ngay** bỏ qua cache RAM và lấy dữ liệu mới.
- Có thể đổi thời gian cache bằng `CODEX_SESSION_MANAGER_QUOTA_TTL` (giây, tối
  thiểu 60 giây).

App không còn đọc `Cache.json` của Rainmeter và không phụ thuộc API 9router.

File `.csm` chứa token đã mã hóa bằng Windows DPAPI. Nó chỉ giải mã được bằng
đúng tài khoản Windows đã tạo backup. Metadata tên, email và quota được để rõ
để giao diện có thể lập danh sách mà không giải mã token.

File `.agy` chỉ chứa credential đăng nhập và scope của Antigravity, được gói
chung rồi mã hóa DPAPI. Nó không chứa conversations, brain, cookie, cache hay
dữ liệu dự án.

## Quy tắc an toàn

- Tạo backup, restore, đăng xuất và xóa backup đều đóng Codex trước và mở lại sau.
- Restore tự backup phiên hiện tại trước khi ghi đè.
- Khi thao tác với Antigravity, app đóng toàn bộ cây tiến trình AGY trước khi
  ghi credential/scope và khởi động lại sau khi hoàn tất. Nếu ghi thất bại,
  credential và scope cũ được rollback.
- Khi backup lại cùng một email, app ghi đè bản cũ; mỗi tài khoản chỉ giữ một
  backup mới nhất. File mới được ghi nguyên tử trước khi các bản trùng bị dọn.
- Đăng xuất an toàn tự backup trước rồi chỉ xóa cache cục bộ; không gọi
  `codex logout`, vì lệnh đó có thể làm mất hiệu lực cache muốn giữ lại.
- File được ghi qua file tạm rồi thay thế nguyên tử để tránh file dở dang.
- Nếu Windows tạm khóa file, bước thay thế được thử lại tối đa 6 lần trong
  khoảng 1,55 giây. Nếu vẫn thất bại, app giữ nguyên bản cũ và dọn file tạm.
- Tài khoản Windows chạy app cần quyền **Modify** trên `tool\backups` và các
  file bên trong. Chỉ có quyền Write là chưa đủ để thay thế backup đã tồn tại;
  trường hợp đó có thể báo `WinError 5` ở bước đổi tên file tạm.
