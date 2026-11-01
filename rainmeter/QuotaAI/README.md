# Quota AI cho Rainmeter

Skin tự chứa cho Codex Session Manager 1.2.0 trở lên; không cần cài cả VietSuite.

## Cài đặt

1. Cài Rainmeter 4.5 trở lên trên Windows.
2. Chép thư mục `QuotaAI` vào thư mục Skins, thường là `Documents/Rainmeter/Skins`.
3. Refresh all trong Rainmeter rồi mở `QuotaAI/Quota.ini`.
4. Chuột phải skin → **Chọn Session Manager**, chọn EXE portable hoặc `main.py`. Bản nguồn cần môi trường `.venv` theo hướng dẫn ứng dụng.
5. Bấm làm mới quota. Skin tự làm mới mỗi 5 phút và kéo được tới vị trí mong muốn.

Bản portable không cần Python. Skin đọc JSON report của EXE, không cần stdout hoặc đường dẫn F: của máy phát triển. Nếu đặt nguyên `rainmeter/QuotaAI` bên trong thư mục EXE, helper cũng thử tìm EXE ở thư mục gốc.

Bấm tài khoản để chuyển phiên, hộp xác nhận luôn hỏi trước. Antigravity có thể cần UAC nếu đang chạy quyền quản trị. Phiên hiện tại được Session Manager sao lưu trước khi chuyển.

## Dữ liệu và giới hạn

- `Cache.json`, `Data.inc` và `SessionSwitchStatus.inc` được tạo trên máy, có thể chứa email/quota; không đăng chúng lên GitHub. Bản phát hành chỉ có Data.inc/status mẫu rỗng.
- `Settings.inc` bản phát hành có đường dẫn trống. Sau khi cấu hình, không ghi đè bằng Settings.inc mẫu khi cập nhật.
- Không có token trong lệnh gọi hoặc skin. Token/backup do Session Manager quản lý.
- Quota không đọc được phải hiện chưa có dữ liệu, không coi là đầy. Antigravity có quota theo model, tùy nguồn ứng dụng cung cấp.
- RunCommand là plugin kèm Rainmeter; helpers dùng Windows PowerShell.
- INI/INC dùng UTF-16LE BOM; PowerShell dùng UTF-8 BOM. Giữ encoding khi sửa tiếng Việt.
- Không kèm credential, cache cá nhân hoặc script 9router cũ. Biểu tượng chỉ nhận diện nhà cung cấp; thương hiệu thuộc chủ sở hữu tương ứng.

Với Antigravity, hai thanh hiển thị cửa sổ quota hạn chế nhất của nhóm GEM và 3P. Tooltip giữ đủ cửa sổ trả về, ví dụ 5H và 7D. Không nhân đôi hai thanh GEM khi 3P có dữ liệu.
