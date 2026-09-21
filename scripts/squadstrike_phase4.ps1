param(
    [Parameter(Mandatory=$true)][string]$Apk,
    [string]$Out = ".\work"
)

$ErrorActionPreference = "Stop"

python -m pip install -e .
New-Item -ItemType Directory -Force -Path $Out | Out-Null

il2cppapk2ios extract $Apk $Out
il2cppapk2ios translate-phase4 "$Out\libil2cpp.so" "$Out\libil2cpp-phase4.dylib" --json |
    Tee-Object -FilePath "$Out\phase4-report.json"
il2cppapk2ios generate-shim "$Out\libil2cpp.so" "$Out\shim" --json |
    Tee-Object -FilePath "$Out\shim-report.json"

Write-Host ""
Write-Host "Phase 4 dylib: $Out\libil2cpp-phase4.dylib"
Write-Host "Shim sources: $Out\shim"
