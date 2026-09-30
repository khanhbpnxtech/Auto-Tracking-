#!/bin/bash
# Biên dịch AutoTrackingApp.swift thành binary, đặt vào Auto Tracking Test.app/Contents/MacOS/.
# Chạy lại script này (rồi commit + push) mỗi khi sửa file .swift.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

APP="../Auto Tracking Test.app"
OUT="$APP/Contents/MacOS/launcher"

# swiftc with no -target embeds THIS machine's SDK default as the binary's minimum required
# macOS version (LC_BUILD_VERSION) — that's independent of, and overrides, Info.plist's
# LSMinimumSystemVersion. Building on a machine with a newer SDK silently baked in "needs macOS
# 26+", so the app refused to even launch on a colleague's macOS 15.7.3 ("You can't use this
# version of the application... with this version of macOS") despite Info.plist saying 11.0.
# Pin an explicit, low, broadly-compatible target for both architectures instead, and build a
# universal binary (arm64 + Intel) since colleagues' Macs aren't all Apple Silicon.
MIN_OS=11.0
swiftc -O AutoTrackingApp.swift -o "$OUT.arm64" -target "arm64-apple-macosx$MIN_OS" \
  -framework Cocoa -framework WebKit
swiftc -O AutoTrackingApp.swift -o "$OUT.x86_64" -target "x86_64-apple-macosx$MIN_OS" \
  -framework Cocoa -framework WebKit
lipo -create "$OUT.arm64" "$OUT.x86_64" -output "$OUT"
rm -f "$OUT.arm64" "$OUT.x86_64"

chmod +x "$OUT"
codesign --sign - --force --deep "$APP"
echo "OK -> $OUT"
