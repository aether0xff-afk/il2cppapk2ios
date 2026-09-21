param(
    [Parameter(Mandatory=$true)][string]$Apk,
    [string]$Out = ".\work"
)

$ErrorActionPreference = "Stop"

python -m pip install -e .
New-Item -ItemType Directory -Force -Path $Out | Out-Null

il2cppapk2ios extract $Apk $Out
il2cppapk2ios translate-phase3 "$Out\libil2cpp.so" "$Out\libil2cpp-phase3.dylib" --json |
    Tee-Object -FilePath "$Out\phase3-report.json"

Write-Host ""
Write-Host "Phase 3 artifact: $Out\libil2cpp-phase3.dylib"
Write-Host "External symbols are routed to @rpath/libbionic_shim.dylib."
Write-Host "The artifact is not runnable until the shim/runtime work is complete."
