from __future__ import annotations

from pathlib import Path
import re
import zipfile

UNITY_VERSION_RE = re.compile(rb"20\d\d\.\d+\.\d+[abfp]\d+")


class ApkError(ValueError):
    pass


class UnityApk:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not zipfile.is_zipfile(self.path):
            raise ApkError("not a valid APK/ZIP")

    def names(self) -> list[str]:
        with zipfile.ZipFile(self.path) as zf:
            return zf.namelist()

    def abis(self) -> list[str]:
        result = set()
        for name in self.names():
            parts = name.split("/")
            if len(parts) >= 3 and parts[0] == "lib" and name.endswith(".so"):
                result.add(parts[1])
        return sorted(result)

    def native_libs(self, abi: str) -> list[dict]:
        prefix = f"lib/{abi}/"
        with zipfile.ZipFile(self.path) as zf:
            out = []
            for info in zf.infolist():
                if info.filename.startswith(prefix) and info.filename.endswith(".so"):
                    out.append({
                        "name": info.filename[len(prefix):],
                        "size": info.file_size,
                        "path": info.filename,
                    })
            return sorted(out, key=lambda x: x["name"])

    def metadata_entries(self) -> list[dict]:
        with zipfile.ZipFile(self.path) as zf:
            return [
                {"path": i.filename, "size": i.file_size}
                for i in zf.infolist()
                if i.filename.endswith("global-metadata.dat")
            ]

    def unity_version(self) -> str | None:
        candidates = [
            "assets/bin/Data/globalgamemanagers",
            "assets/bin/Data/data.unity3d",
            "assets/bin/Data/Resources/unity default resources",
        ]
        with zipfile.ZipFile(self.path) as zf:
            names = set(zf.namelist())
            for name in candidates:
                if name not in names:
                    continue
                data = zf.read(name)[:4 * 1024 * 1024]
                match = UNITY_VERSION_RE.search(data)
                if match:
                    return match.group().decode("ascii")
        return None

    def report(self) -> dict:
        return {
            "apk": str(self.path),
            "size": self.path.stat().st_size,
            "unity_version": self.unity_version(),
            "abis": self.abis(),
            "arm64_libs": self.native_libs("arm64-v8a"),
            "metadata": self.metadata_entries(),
        }

    def extract_arm64(self, output_dir: str | Path) -> list[Path]:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        wanted = {
            "lib/arm64-v8a/libil2cpp.so",
            "lib/arm64-v8a/libunity.so",
            "lib/arm64-v8a/libmain.so",
        }
        extracted = []
        with zipfile.ZipFile(self.path) as zf:
            names = set(zf.namelist())
            wanted.update(n for n in names if n.endswith("global-metadata.dat"))
            for name in sorted(wanted & names):
                target = out / Path(name).name
                target.write_bytes(zf.read(name))
                extracted.append(target)
        return extracted
