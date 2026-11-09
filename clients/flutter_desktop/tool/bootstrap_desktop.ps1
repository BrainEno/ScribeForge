$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot/..

# Resolve dependencies against the official pub.dev / Google storage.
# Third-party mirrors (e.g. pub.flutter-io.cn) re-pack some archives
# (pdfrx, pdfrx_engine), so pub aborts with "wrong content-hash" even though
# the versions are fine. The official endpoints serve archives whose hashes
# match the version listings. Set PUB_HOSTED_URL yourself first to override.
if (-not $env:PUB_HOSTED_URL) {
  $env:PUB_HOSTED_URL = "https://pub.dev"
}
if (-not $env:FLUTTER_STORAGE_BASE_URL) {
  $env:FLUTTER_STORAGE_BASE_URL = "https://storage.googleapis.com"
}

flutter --version
flutter create --platforms=windows,macos,linux --project-name scribeforge_desktop .
flutter pub get
