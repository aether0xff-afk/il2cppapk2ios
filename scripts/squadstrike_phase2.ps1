param(
    [Parameter(Mandatory=$true)][string]$Apk,
    [string]$Out = ".\work"
)

$ErrorActionPreference = "Stop"

python -m pip install -e .
New-Item -ItemType Directory -Force -Path $Out | Out-Null

il2cppapk2ios extract $Apk $Out
il2cppapk2ios elf-report "$Out\libil2cpp.so" --json | Tee-Object -FilePath "$Out\libil2cpp-elf-report.json"
il2cppapk2ios translate-phase2 "$Out\libil2cpp.so" "$Out\libil2cpp-phase2.dylib" --json | Tee-Object -FilePath "$Out\phase2-report.json"

Write-Host ""
Write-Host "Phase 2 artifact: $Out\libil2cpp-phase2.dylib"
Write-Host "This is intentionally not runnable yet; inspect phase2-report.json."
