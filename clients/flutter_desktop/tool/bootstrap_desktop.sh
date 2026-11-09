#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."

# Resolve dependencies against the official pub.dev / Google storage.
# Third-party mirrors (e.g. pub.flutter-io.cn) re-pack some archives
# (pdfrx, pdfrx_engine), so pub aborts with "wrong content-hash" even though
# the versions are fine. The official endpoints serve archives whose hashes
# match the version listings. Export PUB_HOSTED_URL yourself first to override.
: "${PUB_HOSTED_URL:=https://pub.dev}"
: "${FLUTTER_STORAGE_BASE_URL:=https://storage.googleapis.com}"
export PUB_HOSTED_URL FLUTTER_STORAGE_BASE_URL

flutter --version
flutter create --platforms=windows,macos,linux --project-name scribeforge_desktop .
flutter pub get
