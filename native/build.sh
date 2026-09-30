#!/bin/bash
# Biên dịch AutoTrackingApp.swift thành binary, đặt vào Auto Tracking Test.app/Contents/MacOS/.
# Chạy lại script này (rồi commit + push) mỗi khi sửa file .swift.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

APP="../Auto Tracking Test.app"
OUT="$APP/Contents/MacOS/launcher"

swiftc -O AutoTrackingApp.swift -o "$OUT" \
  -framework Cocoa -framework WebKit

chmod +x "$OUT"
codesign --sign - --force --deep "$APP"
echo "OK -> $OUT"
