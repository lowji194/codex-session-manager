# Đóng gói portable cho Windows

Quy trình này đóng gói Python, các thư viện pywebview/pythonnet và ba tệp giao diện
`web/index.html`, `web/styles.css`, `web/app.js` bằng PyInstaller dạng thư mục.
Không nhập module `main`, không khởi động ứng dụng và không sử dụng dữ liệu phiên
đang có trong `tool/`. Chỉ thư mục kết quả do PyInstaller tạo mới được đưa vào ZIP.

## Build tại máy Windows

Dùng Windows x64, Python 3.12 64-bit và môi trường build riêng:

```powershell
py -3.12 -m venv .build-venv
.build-venv\Scripts\python.exe -m pip install -r requirements-build.txt
.build-venv\Scripts\python.exe scripts/build_portable.py --version 1.0.0
```

Lệnh trên tạo bản nhẹ, dùng WebView2 đã cài trên máy đích. Để tạo bản kèm runtime
offline, dùng helper tải bản Microsoft đã pin, rồi truyền đường dẫn kết quả:

```powershell
$runtimeFolder = & ./scripts/fetch_webview2.ps1
.build-venv\Scripts\python.exe scripts/build_portable.py --version 1.0.0-offline --webview2-runtime $runtimeFolder
```

Lệnh kiểm tra đầu vào không đóng gói và không chạy ứng dụng:

```powershell
python scripts/build_portable.py --version 1.0.0 --dry-run
```

Kết quả: `dist/CodexSessionManager-1.0.0-windows-x64.zip` và tệp `.zip.sha256`.
Trong ZIP có `CodexSessionManager.exe`, `_internal/`, hướng dẫn `BAT_DAU.txt` và
`manifest.json` chứa SHA-256 từng tệp. Giải nén toàn bộ; không di chuyển riêng EXE.
Lệnh từ chối ghi đè ZIP đã tồn tại. Đổi nhãn phiên bản hoặc thư mục đầu ra khi build lại.
Thư mục trung gian `.build/portable/<id>/` được giữ để kiểm tra lỗi build.

## WebView2

Python được đóng gói; Microsoft Edge WebView2 là thành phần riêng.

- Mặc định ZIP yêu cầu WebView2 Runtime đã được cài trên máy đích.
- Có thể cung cấp **Fixed Version Runtime** đã tải và giải nén từ Microsoft.
  Đường dẫn phải chứa trực tiếp `msedgewebview2.exe`, đúng kiến trúc của bản build.
  Script không tải hoặc chạy trình cài runtime, không giả định trình duyệt Edge
  đã cài đồng nghĩa mọi máy đích đều đủ runtime.

Helper `scripts/fetch_webview2.ps1` pin **156.0.4314.58 x64** từ miền phân phối
Microsoft, kiểm tra CAB SHA-256
`857DCFD6F7035508A29645FA2978C3A6A841F220DF77CC366D318894E19F4792`, giải nén bằng
`expand.exe` của Windows, xác minh Authenticode `Valid` của Microsoft Corporation,
phiên bản tệp và PE x64. Helper không khởi chạy runtime hoặc ứng dụng. Đầu ra thành
công duy nhất là đường dẫn tuyệt đối tới
`.build/dependencies/WebView2Runtime/Microsoft.WebView2.FixedVersionRuntime.156.0.4314.58.x64`.
Lỗi mạng, checksum hoặc chữ ký làm dừng quy trình; không tự thay bằng bản khác.

Helper tải điều khoản từ API Microsoft ở
`https://developer.microsoft.com/microsoft-edge/api/eula/webview2?locale=en-us&fixed=true`
và lưu `EULA-WebView2-FixedVersion.html` cạnh thư mục runtime, kèm
`WebView2-runtime-source.json` ghi nguồn và các hash đã kiểm tra. Khi đóng gói runtime,
build script giữ hai tệp này ở thư mục gốc trong ZIP nếu chúng có mặt. Người đóng
gói vẫn phải tuân thủ điều khoản phân phối của Microsoft.

```powershell
.build-venv\Scripts\python.exe scripts/build_portable.py --version 1.0.0-with-runtime --webview2-runtime C:\BuildDependencies\WebView2Fixed-x64
```

Runtime được sao chép vào `CodexSessionManager/WebView2Runtime/`. Phần khởi động
ứng dụng dùng `webview.settings['WEBVIEW2_RUNTIME_PATH']` tới thư mục này trước
khi khởi tạo cửa sổ WebView2. Cấu hình runtime rõ ràng do người dùng cung cấp phải
được ưu tiên. Đường dẫn tài nguyên frozen trong `main.py` cần tách `sys._MEIPASS`
(giao diện) khỏi thư mục EXE (dữ liệu portable).

`build_portable.py` chỉ xác nhận cấu trúc thư mục runtime được truyền vào;
**helper tải riêng** mới thực hiện kiểm tra hash, chữ ký và kiến trúc. Runtime tự
cung cấp ngoài helper phải được người phát hành xác minh. Người phát hành phải chọn đúng bản x64/ARM64,
giữ nguyên các thông báo/giấy phép đi kèm và kiểm tra máy Windows sạch trước khi
tuyên bố ZIP hoạt động hoàn toàn không cần cài thêm thành phần. Fixed Runtime
cần được cập nhật cùng các bản phát hành để nhận bản vá. ZIP không kèm runtime
không phải bộ cài WebView2 offline.

## GitHub Actions

Workflow `Build Windows portable` chạy thủ công hoặc khi đẩy tag `v*`. Nó build
Windows x64 và tải ZIP/checksum thành artifact, không chạy ứng dụng và không tự
tạo GitHub Release. Mặc định CI **kèm WebView2 Fixed Runtime đã pin**, cả trên tag.
Khi chạy thủ công, bỏ chọn `include_runtime` để tạo bản nhẹ dùng runtime cài trên máy đích.
Không đặt dữ
liệu `tool/`, `config.local.json`, phiên, khóa, bản ZIP cũ hoặc bản sao lưu vào source phát hành.

## Yêu cầu máy sử dụng

- Windows 11 x64 được khuyến nghị. Máy Windows 10 x64 phải còn đáp ứng yêu cầu của
  phiên bản WebView2 và Codex/Antigravity đang sử dụng; chưa chứng minh tương thích
  toàn bộ các bản Windows 10 bằng việc đóng gói trên Windows CI.
- pywebview Windows dùng pythonnet/WinForms, nên cần .NET Framework 4.8 hoặc bản
  tương thích mới hơn đã có trong hệ điều hành. ZIP này không chứa bộ cài .NET.
- Không cần cài Python. Bản kèm Fixed Runtime không cần cài WebView2 riêng; bản nhẹ
  cần WebView2 Runtime của hệ thống.
- Codex và/hoặc Antigravity phải được cài, có phiên đăng nhập phù hợp trước khi quản
  lý phiên của ứng dụng đó. ZIP không kèm ứng dụng Codex/Antigravity hoặc tài khoản.
- Thư mục giải nén cần quyền ghi để lưu dữ liệu portable. Không dùng thư mục hệ thống
  chỉ đọc. Các thao tác đóng/mở ứng dụng đích còn phụ thuộc quyền của tiến trình đó.

## Giới hạn kiểm chứng

Build thành công chỉ chứng minh đóng gói thành công; vẫn cần kiểm tra EXE trên
Windows sạch, WebView2, quyền ghi dữ liệu, giao diện và luồng bridge. Mã build
không ký số EXE; Windows SmartScreen có thể yêu cầu xác minh nhà phát hành.
