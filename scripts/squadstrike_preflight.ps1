param(
    [Parameter(Mandatory=$true)][string]$Apk,
    [string]$Out = ".\work"
)

$ErrorActionPreference = "Stop"

python -m pip install -e .
il2cppapk2ios apk-report $Apk --json
il2cppapk2ios extract $Apk $Out
il2cppapk2ios elf-report "$Out\libil2cpp.so" --json
il2cppapk2ios elf-report "$Out\libunity.so" --json
