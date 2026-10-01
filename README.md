# Auto Tracking Test — Realtime (local)

Local server tự tail `adb logcat`, parse các dòng `logEvent: <event> - Bundle[{...}]`,
đối chiếu với file spec Excel (`spec.xlsx`), và hiển thị PASS/FAIL realtime trên dashboard
chạy ở `http://127.0.0.1:8765`.

## 0. Cài đặt (lần đầu trên 1 máy mới)

Mỗi người dùng tool này cần chạy server **trên chính máy của mình** (vì tool cần adb truy cập
trực tiếp thiết bị cắm vào máy đó). `adb` và thư viện Python `openpyxl` đã **đóng gói sẵn** trong
`Auto Tracking Test.app` (`Contents/Resources/vendor/`) — không cần cài 2 thứ đó nữa, dù chạy
qua app hay qua Terminal. Chỉ cần:

1. `git clone` repo này (xin link từ người quản lý tool):
   ```bash
   git clone <link-repo> tracking-auto-test
   ```
2. Cắm điện thoại Android qua USB, bật **Developer Options → USB debugging**, bấm "Allow"
   khi máy hỏi cấp quyền debug cho máy tính.
3. Mở **`Auto Tracking Test.app`** trong thư mục vừa clone (xem phần Desktop App ngay dưới) —
   xong, không cần cài gì thêm.

Chỉ cần cài tay thêm khi:
- **Chạy qua Terminal** (`python3 server.py`) thay vì mở app — lúc đó PATH/PYTHONPATH không tự
  trỏ vào hàng vendor, cần `python3 -m pip install -r requirements.txt` và có `adb` riêng
  (`brew install android-platform-tools`) như một máy dev bình thường.
- **Quay video màn hình máy** — cần `ffmpeg` (`brew install ffmpeg`); không đóng gói được vì
  ffmpeg của Homebrew phụ thuộc ~18 thư viện khác. Thiếu thì mọi thứ khác vẫn chạy bình thường,
  chỉ riêng bước "sửa file mp4 để Trim được" bị bỏ qua.

### Desktop App (mở 1 cú nhấp, cửa sổ riêng — không phải mở trình duyệt)

Trong thư mục vừa clone có sẵn **`Auto Tracking Test.app`** — double-click để chạy: tự bật
server (nếu chưa chạy) rồi hiện dashboard trong **1 cửa sổ app thật** (dùng WKWebView của macOS,
`native/AutoTrackingApp.swift`) — không thanh địa chỉ, không tab, không phải Safari/Chrome. Quit
app (Cmd+Q hoặc đóng cửa sổ) thì server cũng tự tắt theo (nếu chính app này khởi động nó).

Nếu cổng 8765 đã có server chạy sẵn, app chỉ dùng lại khi đó đúng là server **của chính thư mục
này và đang chạy đúng code hiện có trên đĩa** (hỏi qua `/api/whoami`). Server của 1 bản tool khác
trên máy (vd. bản giải nén cũ còn sót trong Documents), hoặc server cùng thư mục nhưng còn chạy code
cũ sau khi `git pull` tay, sẽ bị tự tắt để app chạy server đúng của mình. Nếu cổng bị 1 chương
trình không phải tool này chiếm thì app báo lỗi chứ không tự tắt chương trình đó.

Lần đầu chạy trên máy mới, macOS có thể hỏi cấp quyền truy cập thư mục (Desktop/Documents...) —
bấm **Allow/Cho phép**. Trong lúc hộp thoại này chưa được trả lời, server đứng im (dashboard hiện
"Đang khởi động server..." mãi) — kiểm tra xem hộp thoại có bị che sau cửa sổ khác không. Tool để
trong Documents thì hộp thoại này hiện lại mỗi lần app được build lại (chữ ký app đổi), nên clone
ra ngoài Documents (vd. `~/tracking-auto-test`) sẽ đỡ phiền hơn.

Sửa xong `native/AutoTrackingApp.swift` thì biên dịch lại bằng `native/build.sh` (cần Xcode
Command Line Tools, có `swiftc` sẵn) rồi commit cả app đã build — đồng nghiệp không cần tự biên
dịch gì, bấm Update là nhận app mới luôn. `build.sh` build universal (Intel + Apple Silicon) và
ghim sẵn `-target macosx11.0` — swiftc không có cờ này sẽ tự lấy SDK máy đang build làm bản macOS
tối thiểu bắt buộc, nên build trên máy cài macOS/Xcode mới sẽ ra app không mở được trên máy đồng
nghiệp còn dùng macOS cũ hơn (báo "You can't use this version of the application..." dù
`LSMinimumSystemVersion` trong Info.plist ghi 11.0 — key đó chỉ mang tính khai báo, không phải
cái macOS thật sự kiểm tra).

> Nếu đồng nghiệp gặp đúng lỗi "You can't use this version of the application 'Auto Tracking
> Test' with this version of macOS" khi mở app: bản họ đang có build từ trước khi `build.sh` ghim
> `-target` (chưa có fix này) — cần `git pull` (qua Terminal, vì lúc này app còn chưa mở được để
> tự bấm Update) rồi mở lại.

> Đã kiểm chứng: cửa sổ hiện đúng, dashboard tải và chạy đúng bên trong (đọc bằng Accessibility
> API vì môi trường build không chụp được ảnh màn hình), quit app tắt server đúng như mong đợi.
> Riêng tính năng **Màn hình máy** (giải mã video bằng WebCodecs) chưa tự bấm-thử được trong
> WKWebView ở môi trường này — nên bấm thử tab đó 1 lần sau khi cài, nếu không hiện hình thì báo
> lại (Safari/WebKit bản máy bạn có thể chưa hỗ trợ đủ WebCodecs).

App tự thêm `Contents/Resources/vendor/bin` (adb) và `vendor/python` (openpyxl) vào
PATH/PYTHONPATH trước khi chạy `server.py` — đã kiểm chứng bằng cách giả lập máy trắng (không
Homebrew, không pip-install gì) và chạy được bình thường. Muốn cập nhật bản `adb` đóng gói sau
này (ví dụ khi cần chạy trên chip đời mới hơn): copy đè
`$(brew --prefix)/bin/adb` vào `Auto Tracking Test.app/Contents/Resources/vendor/bin/adb`, commit,
push — đồng nghiệp bấm Update là có bản mới. Riêng `openpyxl` hiếm khi cần cập nhật.

> **Lưu ý quan trọng:** nếu clone repo vào **trong `~/Desktop`** hoặc `~/Documents` (macOS coi
> đây là thư mục "nhạy cảm"), có máy sẽ **im lặng chặn** app đọc file thay vì hỏi quyền (app báo
> lỗi "Operation not permitted" khi mở, không có hộp thoại xin quyền nào cả) — mình đã gặp lỗi
> này khi test. Nếu double-click app mà dashboard không mở, **cách chắc ăn nhất: clone vào thư
> mục khác**, ví dụ thẳng trong home — `git clone <link-repo> ~/tracking-auto-test` — thư mục
> này không bị chặn kiểu đó. Cách khác: vào **System Settings → Privacy & Security → Files and
> Folders**, tìm "Auto Tracking Test" và bật quyền truy cập Desktop/Documents thủ công.
>
> App chưa được Apple ký (không có tài khoản Apple Developer) — macOS có thể chặn lần mở đầu
> tiên với cảnh báo "không xác định được nhà phát triển". Cách mở: **click phải (hoặc
> Control-click) vào app → Open → Open** (chỉ cần làm 1 lần).
>
> App phải luôn nằm **ngay trong** thư mục tool (cạnh `server.py`) — nó tự tìm `server.py` bằng
> đường dẫn tương đối tới chính nó, copy app ra chỗ khác một mình sẽ báo lỗi.

### Cập nhật bản mới — nút "Update" trên dashboard

Góc trên bên trái dashboard có ô hiện **version hiện tại** (vd `v1.1`) và nút **Update**:
bấm vào để kiểm tra bản mới trên GitHub, nếu có sẽ hỏi xác nhận rồi tự `git pull` + tự Quit/mở
lại app (không cần mở Terminal, không cần double-click lại app tay). Vài lưu ý:

- Chỉ hoạt động khi thư mục tool là 1 **git clone có remote** — nếu ai đó copy folder tay (không
  qua git clone) sẽ không thấy ô version/nút Update xuất hiện.
- Nếu bạn có sửa tay code ở máy mình (chưa commit), nút Update sẽ báo lỗi và **không tự ghi đè**
  — tránh mất code đang sửa dở. Muốn update thì `git stash` hoặc commit tạm trước.
- Bấm Update xong app sẽ tự Quit rồi mở lại hẳn (không chỉ reload trang) — luôn vậy dù bản mới
  chỉ đổi web hay đổi cả vỏ app, để chắc chắn dùng đúng bản mới nhất mọi lúc.
- **Số version** (`v1.0`, `v1.1`, ...) đọc từ file `VERSION` (1 dòng, tự sửa tay) ở thư mục gốc —
  chỉ để hiện cho dễ nhìn, còn việc check/tải bản mới vẫn dựa vào git commit thật (hover vào số
  version để xem đúng commit). Nhớ bump số này lên mỗi khi muốn đồng nghiệp nhận ra "có bản mới
  thật sự" thay vì chỉ thấy mã commit khó nhớ.

## 1. Chuẩn bị file spec — tự đồng bộ từ Lark "Tracking Auto"

Spec gốc để đối chiếu nằm ở sheet **Tracking Auto** trên Lark Wiki:
[technify.sg.larksuite.com/wiki/PYxGwDEFAizBpIk6DkolwgfIguT](https://technify.sg.larksuite.com/wiki/PYxGwDEFAizBpIk6DkolwgfIguT)
(XTECH AI → QC department → Checklist management).

**Mặc định server tự kéo sheet này về mỗi 60s** (`fetch_spec.py` + `lark-cli`, xem
`LARK_SPEC_URL`/`LARK_FETCH_INTERVAL` trong `server.py`) — sửa spec trực tiếp trên Lark, tối đa
1 phút sau dashboard tự cập nhật, **không cần export/tải file gì cả**. Bấm icon Refresh ở bất kỳ
tab nào (Other events / Ads tracking / Auto Event Tracking) cũng ép đồng bộ lại ngay, không cần
chờ 60s. File vẫn được lưu ở `~/Documents/tracking-spec/lark-<sheet_id>.xlsx` (tool tự tạo thư
mục này) — đó chỉ là nơi lưu tạm để `SpecStore` đọc, không phải thứ bạn cần tự tay quản lý nữa.

**Yêu cầu:** máy đã cài và đăng nhập `lark-cli` — **cài qua npm, không phải Homebrew**
(`lark-cli` thật ra là gói `@larksuite/cli`):
```bash
npm install -g @larksuite/cli   # cần Node.js trước — chưa có thì `brew install node`
lark-cli auth login
```
kiểm tra bằng `lark-cli auth status` hoặc `lark-cli doctor`. Cần quyền đọc sheet đó trên tài
khoản Lark vừa đăng nhập. Nếu thiếu — server vẫn chạy bình thường, chỉ in 1 dòng cảnh báo
`[lark] Không tự đồng bộ được...` trong log rồi tự thử lại mỗi 60s, không crash. Trong lúc đó
(hoặc trên máy chưa cài `lark-cli`), quay lại cách cũ: **Download As → Excel (.xlsx)** từ sheet
trên Lark, thả file vào `~/Documents/tracking-spec` — tool tự lấy file `.xlsx` mới nhất trong
thư mục (không cần đè/đổi tên file), và **tự việc tự động đồng bộ Lark ở trên không ảnh hưởng gì
đến cách này**, cả hai cùng đổ vào chung 1 thư mục.

> Trên máy khác (đồng nghiệp dùng lại tool), mỗi người tự cài + đăng nhập `lark-cli` trên máy họ
> (không chia sẻ token qua code) — không cần chỉnh `LARK_SPEC_URL` hay đường dẫn tuyệt đối nào,
> trừ khi Technify đổi sang trang Lark khác.
>
> Muốn tắt tự đồng bộ (offline, hoặc chỉ muốn dùng file tự export): chạy server với
> `--spec <đường-dẫn-tới-1-file.xlsx>` (chỉ trỏ vào file, không phải thư mục) — khi đó
> `lark_spec_watcher` không khởi động, quay về hoàn toàn thủ công.

Cấu trúc cột mà tool đọc (khớp với sheet Tracking Auto thật): `Feature`, `Event name`,
`Param name`, `Button name`, `Screen name`, `Overlay name`, `Valid Value`, `Định nghĩa`,
`App Version`. Tool tự nhận sheet có tên chứa "tracking" trong file export (nếu file có nhiều
tab), nên không cần chỉnh gì thêm sau khi export.

- Cột `Valid Value` (nếu có ghi theo dạng `key: mô tả` hoặc `key = mô tả`, vd
  `category: id category`) sẽ được tool tách ra tên key để kiểm tra event live có đủ param đó
  không — tương tự cột `Bundle Params` ở bản template cũ.
- Có thể trỏ tool đọc 1 file cụ thể (không dùng cơ chế thư mục) hoặc 1 thư mục khác bằng
  `--spec <đường dẫn>` khi chạy server.

## 2. Chạy server

```bash
cd tracking-auto-test   # thư mục bạn vừa copy về
python3 server.py
```

Mặc định `--spec` trỏ tới thư mục `~/Documents/tracking-spec` (tự tạo nếu chưa có) — không cần
truyền gì thêm nếu bạn theo đúng bước ở mục 1.

- Cắm điện thoại qua USB, bật USB debugging, bấm "Allow" trên máy.
- **Server tự động nhận device đang cắm tại thời điểm đó** — không cần chỉ định trước.
  Rút máy ra / cắm máy khác vào giữa chừng, server tự phát hiện và chuyển sang tail log của
  máy mới mà không cần restart.
- **Nhiều device cùng lúc (như "Running Devices" của Android Studio):** góc trên phải dashboard
  tự hiện thêm 1 ô chọn device ngay khi có từ 2 máy/máy ảo trở lên đang cắm — chọn máy nào thì
  server chuyển sang tail log máy đó ngay lập tức, không cần rút máy hay restart. Không chọn gì
  (để "— tự chọn —") thì quay lại hành vi mặc định: tự nhận nếu chỉ có 1 máy, hoặc chờ bạn chọn
  nếu có nhiều máy. Cũng có thể ghim cứng bằng `--serial <serial>` khi chạy server (không đổi qua
  ô chọn được nữa, ưu tiên cao nhất — dùng khi CHỈ muốn 1 máy cố định dù cắm thêm máy khác).
- **Máy ảo (emulator):** dashboard tự liệt kê các AVD đã tạo sẵn trong Android Studio (nút "Chạy
  máy ảo" cạnh ô chọn device, chỉ hiện khi máy có cài Android SDK + đã có AVD) — bấm là khởi động,
  mất khoảng 1-2 phút. Tool này **không tạo AVD mới được** (chọn system image, RAM, dung lượng...
  là việc của Android Studio, cần tải image nặng vài GB) — mở Android Studio → Device Manager để
  tạo AVD mới, sau đó quay lại đây là thấy ngay trong danh sách.
- Mặc định lọc log theo tag `TrackingEvent` (đổi bằng `--tag`).
- Mở trình duyệt: **http://127.0.0.1:8765** — góc trên phải dashboard luôn hiện tên model +
  serial của device đang tail log realtime (vd: `TECNO KM4 (148427055L008212)`).

## 3. Test

- Thao tác trên app như bình thường (chuyển màn, bấm nút...). Mỗi dòng `logEvent:` bắt được
  qua logcat sẽ tự xuất hiện trên dashboard theo thời gian thực, kèm verdict:
  - 🟢 **MATCH** — có trong spec, đủ tên định danh + đủ param khai báo.
  - 🟡 **PARTIAL** — khớp được 1 dòng spec nhưng thiếu param được khai, hoặc có param lạ
    chưa khai trong `Bundle Params`.
  - 🔴 **UNKNOWN** — không tìm thấy dòng spec nào khớp (event/tên định danh sai, hoặc thiếu
    trong sheet).
  - Bấm icon **"?"** cạnh ô Unknown để xem lại chú thích 3 verdict này bất cứ lúc nào.
- Cột **Package** hiện package name của app bắn ra event đó (tự resolve từ PID trong log qua
  `/proc/<pid>/cmdline`) — hữu ích khi nhiều app cùng dùng chung tag `TrackingEvent`. Nếu app
  vừa mở/vừa restart, dòng đầu tiên có thể hiện `?` trong lúc resolve, các dòng sau sẽ ra tên
  package bình thường (đã cache theo PID).
- Thả file export mới vào `~/Documents/tracking-spec`, server **tự phát hiện và tự nạp lại**
  trong vài giây — không cần restart, không cần đè/đổi tên file. Nút **"Reload spec file"** trên
  dashboard vẫn còn, dùng khi muốn nạp lại ngay lập tức thay vì chờ vài giây.
- Thanh công cụ mỗi tab có 3 nút icon: **Refresh** (tải lại log từ server và **khớp lại toàn bộ
  log với spec hiện tại** — hữu ích sau khi sửa spec: các dòng UNKNOWN cũ sẽ được chấm lại),
  **Tạm dừng/Tiếp tục** (giữ nguyên bảng khi cần đọc kỹ; bấm Refresh để bù lại phần đã lỡ) và
  **Thùng rác** (xoá log của đúng tab đó, xoá luôn ở server nên Refresh/F5 không hiện lại).
- Cột **Time** hiện giống Android Studio: `2026-09-25 08:39:12.508` (ngày giờ + mili-giây theo
  đồng hồ của thiết bị), dòng dưới là `PID-TID` và độ trễ so với event liền trước (vd `+2.3s`).
  Tool đọc logcat ở format `threadtime` + năm để có đủ các trường này.
- Nút **copy** cạnh Bundle live: copy **thời gian + event + bundle** của dòng đó, theo chế độ đang
  chọn ở nút "Bundle: Pills/JSON" — Pills copy dạng 3 dòng (`giờ PID-TID` / `event` /
  `key=value, key=value`), JSON copy đối tượng `{time, pid_tid, event, bundle}`.
- Tab **Errors** cũng có nút copy: copy `giờ PID-TID` / `LEVEL tag (package)` / nội dung lỗi.
- Bấm **Reload spec file** có phản hồi rõ: nút chuyển "Đang nạp…" → "✓ Đã nạp", khung Spec nháy
  viền xanh kèm dòng "✓ Đã nạp lại lúc HH:MM:SS" (đỏ nếu spec lỗi). Khi server tự nạp lại vì file
  spec mới được thả vào thư mục, khung Spec cũng nháy để bạn biết.
- Khung **Spec** góc phải toolbar: số dòng spec đang dùng + tên file đang nạp (di chuột để xem
  đường dẫn đầy đủ); có nút Reload spec file ngay trong khung.
- 2 ô tìm kiếm riêng biệt, đều hỗ trợ nhiều từ khoá cùng lúc cách nhau bằng dấu `,` hoặc `|`
  (vd: `iaa_loaded | iaa_request`), kết hợp với dropdown filter verdict:
  - Ô 1: lọc theo event / định danh / package.
  - Ô 2: tìm text bên trong Bundle live (vd gõ `ScrHome` để chỉ hiện event có screen đó trong bundle).
- Nút **"Bundle: Pills / JSON"** để chuyển cách hiển thị cột Bundle live giữa dạng tag màu và
  JSON thô (khung JSON hiển thị cỡ chữ lớn, dễ đọc — tiện copy nguyên bundle để dán report).

## Tab "Errors (app)"

- Song song với 2 tab tracking event, tab **Errors (app)** tự bắt các log mức **Error/Fatal**
  (crash, exception, lỗi SDK quảng cáo, lỗi API...) của **đúng app đang test** — dựa trên
  package đã thấy qua tracking event, nên tự động bỏ qua lỗi vặt của hệ thống Android hay app
  khác trên máy mà không cần khai danh sách loại trừ thủ công.
- Các dòng log liên tiếp cùng PID + tag (vd nguyên 1 stack trace crash) được gộp lại thành 1
  entry duy nhất thay vì rã ra từng dòng rời rạc.
- Tab này chỉ hoạt động **sau khi** đã có ít nhất 1 tracking event từ app đó (để tool biết
  "app đang test" là app nào) — mở app và thao tác một chút trước khi trông đợi thấy log lỗi.

## Tab "Auto Event Tracking" (trước gọi là "Coverage (spec)") — test xong chưa?

Tab này liệt kê **mọi dòng của spec** kèm trạng thái trong phiên test hiện tại, cập nhật live:

| Trạng thái | Ý nghĩa |
|---|---|
| **Passed** | Mọi định danh của dòng spec đã từng thấy **MATCH** |
| **Đang test** | Mới thấy một phần — dòng spec có nhiều màn/nút (vd 1 dòng `screen_show` cho 4 màn) và còn thiếu vài cái; cột Ghi chú liệt kê **chưa thấy cái nào** |
| **Failed** | Có định danh chỉ mới thấy **PARTIAL** (thiếu/thừa param); Ghi chú nêu lý do |
| **Chưa test** | Chưa thấy lần nào |

- Có thanh tiến độ "Đã đụng tới X/Y", lọc theo trạng thái / Feature / từ khoá.
- Một event Failed mà sau đó bắn lại đúng (MATCH) thì lật thành Passed (tính là test lại thành công).
- Cột **Feature** được điền xuống cho các dòng trong ô gộp của sheet (chỉ dòng đầu nhóm có giá trị).
- Ô có nhiều giá trị (xuống dòng / dấu phẩy) được tách ra; tên nút dùng lại ở nhiều overlay/màn
  (vd `BtnAllow` ở 2 popup) được phân biệt nhờ `overlay_name`/`screen_name` trong bundle.
- Tab **Xuất báo cáo** (nút trên toolbar): **Excel** (5 sheet: Tổng quan, Coverage, Event không có
  trong spec, Events, Errors), **Markdown** (dán vào ticket/bug) hoặc **CSV** (bảng coverage).
- Ô **"Tracking Auto (Lark)"** trên toolbar mở thẳng trang Lark Wiki — sửa spec ở đó, dashboard tự
  đồng bộ lại mỗi 60s (xem mục 1: cần cài + đăng nhập `lark-cli`; không có thì vẫn dùng cách export
  thủ công như trước, không ảnh hưởng gì khác). Đổi link thì sửa cả 2 chỗ: hằng `COVERAGE_SPEC_URL`
  trong `static/index.html` (nút mở tab) và `LARK_SPEC_URL` trong `server.py` (nơi thực sự fetch).
- Cột **Bundle live**: log thật gần nhất khớp dòng spec đó (MATCH hoặc PARTIAL), kèm nút copy ra
  đúng định dạng `thời gian pid-tid / tên event / param=value, ...` — dùng để dán thẳng vào báo cáo
  hoặc đưa cho automation test mà không cần quay lại tab Other events tìm log gốc.

## Tab "RC Manager" — RemoteConfig app đã fetch

Server thu log tag `RemoteConfigManager` (`fetchRemoteConfig <kiểu>: <key>: <giá trị>`) **ở nền** ngay khi app mở, gom theo từng lần fetch (1 lần mở app = 1 lần). Tab **không tự cập nhật** để khỏi tràn log: chỉ đọc khi **bấm vào tab** hoặc bấm **Refresh**.

- Bảng Key / Kiểu / Giá trị, có ô tìm (key hoặc giá trị, cách nhau `,` hoặc `|`), lọc theo kiểu (boolean / string / long), copy từng dòng hoặc copy cả config dạng JSON.
- Dropdown chọn lần fetch (mới nhất ở trên, giữ tối đa 20 lần). Chọn lần cũ thì Refresh vẫn giữ lần đó.
- Bật tool sau khi app đã mở: tool lấy lại lần fetch còn trong buffer logcat (buffer máy chỉ ~256 KiB nên chỉ giữ được khoảng vài phút).
- Nút thùng rác chỉ xoá dữ liệu RC; không ảnh hưởng các tab khác.
- Giới hạn: app chỉ log độ dài cho giá trị lớn (vd `ads_config: len=13579`), nên tool không thấy nội dung JSON đó — cần app log đầy đủ nếu muốn xem.

## Đẩy kết quả test ngược lên sheet Lark (`push_to_lark.py`)

Sau khi test xong 1 phiên, tự điền kết quả vào sheet Lark — không cần copy-paste tay.

**Ghi gì vào đâu:**
| Trạng thái (Coverage) | QA Test | QA Note |
|---|---|---|
| Passed | `Passed` | chỉ ghi nếu có lần sai trước khi đúng (flaky) |
| Failed (sai ≥ 3 lần = test + 2 lần retest) | `Failed` | lý do: thiếu param / param lạ / sai giá trị |
| Failed nhưng mới sai 1–2 lần | *(bỏ qua, in ra "cần retest")* | |
| Không test được (`qa.py mark`) | *(để trống — dropdown chỉ có Passed/Failed)* | `Không test được: <lý do>` |
| Đang test / Chưa test | *(giữ nguyên)* | |

- Đọc **sheet sống** trên Lark để lấy đúng số dòng thật (khớp theo event + nút/màn + param +
  overlay), không dùng số dòng của file export. Ô nào giá trị không đổi thì không ghi.
- Không truyền `--sheet-url` thì ghi vào đúng sheet mà spec được tải về bằng `fetch_spec.py`.
- `--execute` ghi xong sẽ **đọc lại** các ô để xác nhận (`"mismatch": []` = đúng hết).

**Yêu cầu:** `lark-cli` (`npm install -g @larksuite/cli`), đã `lark-cli config init` bằng App ID/Secret
trong `.env`; quyền đọc `sheets:spreadsheet:read` + quyền ghi **`sheets:spreadsheet:write_only`**
đã được admin XTECH AI duyệt — chưa duyệt thì lệnh ghi báo `missing_scopes`, đó là đúng.

```bash
python3 fetch_spec.py "<link sheet Lark>"   # tải spec thẳng từ Lark vào ~/Documents/tracking-spec
python3 push_to_lark.py                     # DRY-RUN — in từng ô: giá trị cũ -> mới
python3 push_to_lark.py --execute           # ghi thật + đọc lại xác nhận
```

## Dùng từ terminal (`qa.py`) — cho AI tester / không mở trình duyệt

```bash
python3 qa.py status              # tổng + các dòng đã có kết quả
python3 qa.py todo -f "PO request"  # dòng còn phải test (kèm Định nghĩa), gồm cả "retest k/3"
python3 qa.py last 8              # 8 event mới nhất + khớp dòng nào + sai gì
python3 qa.py row 17              # chi tiết 1 dòng + các event đã tính cho nó
python3 qa.py mark 12 "cần gói Premium"   # đánh dấu Không test được
python3 qa.py note 13 "chỉ test trên WiFi" # ghi chú thêm vào QA Note
```

Param chung mà spec không khai (vd `is_paywall` trên mọi `button_click`): chạy server với
`--allow-param is_paywall` để không bị tính là "Param lạ".

Skill Claude Code **`test-xtech-tracking`** (`~/.claude/skills/test-xtech-tracking`) dùng đúng các lệnh
trên: tự lái app trên máy bằng adbx, so logcat với sheet, retest dòng fail 2 lần, đánh dấu dòng không
test được, rồi (sau khi bạn đồng ý) ghi lên Lark.

## Màn hình máy (mirror) — xem, bấm, chụp, quay

Nút **"Màn hình máy"** ở header mở panel bên phải, hiện màn hình máy đang tail log (kéo mép trái panel để đổi độ rộng).

- **Tương tác:** click = tap · giữ ≥ 0,45s = long press · kéo = swipe · 3 nút Back / Home / Recents bên dưới và nút Power (góc phải, bật/tắt màn hình — máy có khoá màn hình thì tắt xong phải tự mở khoá trên máy). Con trỏ trên hình máy là mũi tên đỏ viền trắng. Có hiệu ứng chạm/vuốt vẽ trên hình.
- **Gõ chữ / dán chữ:** bấm vào vùng hình máy (viền sáng xanh báo đang focus) rồi gõ bàn phím thật — gõ tới đâu gửi xuống máy tới đó; phím Enter/Backspace/Tab/mũi tên/Delete/Esc (→Back) cũng hoạt động. **Cmd+V dán được chữ copy từ ngoài** (Chrome, Notes, bất kỳ app nào trên Mac) thẳng vào máy, không cần gõ tay lại. Giới hạn: `adb shell input text` chỉ nhận ASCII qua bảng mã bàn phím US — chữ/dán có dấu tiếng Việt sẽ không lên được trên máy, đây là giới hạn của adb chứ không phải lỗi của tool.
- **Chụp ảnh** (icon máy ảnh): PNG full-res (`adb screencap`) → lưu thẳng **~/Desktop** (`screenshot-<serial>-<ngày-giờ>.png`).
- **Quay video** (chấm đỏ; bấm lại để dừng): bật *Show taps* trên máy trong lúc quay (chạm vật lý hiện chấm; chạm từ web được vẽ hiệu ứng vào video), xong tự trả setting về giá trị cũ → lưu **~/Desktop** (`record-<serial>-<ngày-giờ>.mp4`). File luôn được đẩy qua `ffmpeg` một lượt trước khi lưu (remux nhanh nếu trình duyệt ghi thẳng mp4, chuyển đổi nếu chỉ ghi được webm) — nếu không, file mp4 do trình duyệt tự ghi hay bị lỗi `moov`/duration khiến QuickTime (và nhiều app khác) mở được nhưng **không Trim được**.
- **Cài app:** kéo file **.apk** hoặc **.aab** từ Finder thả vào hình máy — tự cài lên máy đang
  cắm, không cần gõ lệnh. `.apk` cài thẳng qua `adb install -r` (không cần gì thêm). `.aab` cần
  **bundletool** (build ra đúng bộ APK cho máy đang cắm rồi mới cài được) — cài bằng
  `brew install bundletool` (tự kéo theo Java); chưa cài thì báo rõ lỗi ngay trên hình, không
  đụng gì tới các tính năng khác.
- Cách chạy: `adb screenrecord` (H.264) → HTTP → WebCodecs, nên cần Chrome / Edge / Safari mới. Cứ 180s `screenrecord` tự dừng, server bật lại (đứng hình ~0,3s).
- Đo thực tế trên AE9260: 40–55 fps khi cuộn/chuyển màn, trễ từ lúc bấm tới khi hình đổi ~0,2s. Màn hình đứng yên thì không có frame (fps = 0) — bình thường.
- Chỉ nhận request từ `127.0.0.1` / `localhost`, POST cần header `X-Requested-With`. Chưa kiểm tra khi xoay ngang máy.
- **Vì sao đôi khi đứng hình rồi "tua" tới đoạn sau:**
  1. `adb exec-out screenrecord` không đảm bảo tắt được tiến trình `screenrecord` **thật sự trên điện thoại** khi phía Mac dừng kết nối (exec-out không có pty, không forward signal) — để lâu ngày, các tiến trình cũ dồn lại tranh nhau bộ mã hoá video, gây đứng hẳn không hồi được. Server giờ **luôn tắt tiến trình trên máy** (`pkill screenrecord` qua adb shell) mỗi khi bắt đầu/kết thúc 1 phiên, và phiên mới luôn ngắt phiên cũ ngay lập tức thay vì chờ — không để tích tụ nữa.
  2. **App đang test có quảng cáo video** (banner ads không sao, nhưng interstitial/rewarded video, hay quảng cáo test lặp) **dùng chung phần cứng giải/mã video** với `screenrecord` — lúc quảng cáo phát, `screenrecord` phải chờ, gây đứng hình thật; hết quảng cáo mới có hình tiếp, nhìn giống bị "tua". Đây là giới hạn phần cứng của máy, không sửa được từ phần mềm — với app IAA (quảng cáo liên tục) sẽ gặp thường xuyên hơn app thường. Sau bản sửa, việc đứng hình do lý do này **tự hồi phục trong vài giây** khi quảng cáo kết thúc, thay vì đứng vĩnh viễn như lỗi (1).
  3. Việc quay video copy trực tiếp hình đang xem tại mỗi lần vẽ; trước đây dùng `requestAnimationFrame` — trình duyệt **tự tạm dừng rAF khi tab/pane bị ẩn/mất focus**, làm khúc quay bị đứng cứng rồi nhảy thẳng tới hình mới nhất khi lấy lại focus. Đã đổi sang `setInterval` (30fps) — không bị trình duyệt tạm dừng khi ẩn, tối đa bị giảm còn ~1fps chứ không đứng hẳn.

## Giới hạn / lưu ý

- Matching dựa trên tên định danh (`screen_name` / `overlay_name` / `button_name`) — nếu app
  dùng key khác lạ (không phải `button_name`) cho param định danh của `button_click`, cần khai
  đúng tên key đó vào cột **Param name** trong spec để tool nhận diện (xem ví dụ dòng
  `aiphoto_style` trong sheet gốc bạn gửi).
- Việc kiểm tra "đủ/thiếu param" chỉ áp dụng cho các key liệt kê trong `Bundle Params` của
  từng dòng — các key phổ biến (`screen_name`, `prev_screen_name`, `session_number`,
  `overlay_name`, `button_name`) luôn được bỏ qua vì coi là param hệ thống mặc định.
- Nếu app khác cũng dùng tag `TrackingEvent`, log có thể lẫn — nên test 1 app tại 1 thời điểm,
  hoặc đổi `--tag` nếu app log dưới tag khác.
- Dừng server: `Ctrl+C` ở terminal đang chạy, hoặc `pkill -f "server.py --spec"`.
