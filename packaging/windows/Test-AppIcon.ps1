param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$ExpectedIcon
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$exe = (Resolve-Path -LiteralPath $Executable).Path
$expectedPath = (Resolve-Path -LiteralPath $ExpectedIcon).Path
$actual = [System.Drawing.Icon]::ExtractAssociatedIcon($exe)
if ($null -eq $actual) { throw "No executable icon found: $exe" }
$expected = [System.Drawing.Icon]::new($expectedPath, $actual.Size)
$actualBitmap = $actual.ToBitmap()
$expectedBitmap = $expected.ToBitmap()
try {
    # Compare decoded pixels, not compressed resource bytes: Windows may
    # choose a different ICO encoding for the same size.
    $difference = 0.0
    for ($y = 0; $y -lt $actualBitmap.Height; $y++) {
        for ($x = 0; $x -lt $actualBitmap.Width; $x++) {
            $a = $actualBitmap.GetPixel($x, $y)
            $b = $expectedBitmap.GetPixel($x, $y)
            $difference += [Math]::Abs([int]$a.R - [int]$b.R)
            $difference += [Math]::Abs([int]$a.G - [int]$b.G)
            $difference += [Math]::Abs([int]$a.B - [int]$b.B)
        }
    }
    $mean = $difference / ($actualBitmap.Width * $actualBitmap.Height * 3)
    if ($mean -gt 15) { throw "Executable icon does not match ScribeForge branding (mean pixel difference $mean)." }
    Write-Host "Executable icon verified against ScribeForge branding (mean difference $mean)."
} finally {
    $actualBitmap.Dispose()
    $expectedBitmap.Dispose()
    $actual.Dispose()
    $expected.Dispose()
}
