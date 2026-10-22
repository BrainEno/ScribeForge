$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot/..
flutter --version
flutter create --platforms=windows,macos,linux --project-name scribeforge_desktop .
flutter pub get
