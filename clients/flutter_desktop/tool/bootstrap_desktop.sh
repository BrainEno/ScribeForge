#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
flutter --version
flutter create --platforms=windows,macos,linux --project-name scribeforge_desktop .
flutter pub get
