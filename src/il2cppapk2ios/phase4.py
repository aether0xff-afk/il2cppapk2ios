from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
import struct
import tempfile

from .elf import SHN_UNDEF
from .phase3 import (
    Phase3Translator,
    Phase3Error,
    ImageSegment,
    _align,
    _build_version_command,
    _dylib_command,
    _segment_no_sections,
    LC_SEGMENT_64,
    LC_LOAD_DYLIB,
    LC_ID_DYLIB,
    LC_BUILD_VERSION,
    LC_DYLD_INFO_ONLY,
    VM_PROT_READ,
    MH_MAGIC_64,
    CPU_TYPE_ARM64,
    CPU_SUBTYPE_ARM64_ALL,
    MH_DYLIB,
    MH_DYLDLINK,
    MH_TWOLEVEL,
)

S_MOD_INIT_FUNC_POINTERS = 0x9
S_MOD_TERM_FUNC_POINTERS = 0xA
S_ATTR_PURE_INSTRUCTIONS = 0x80000000
S_ATTR_SOME_INSTRUCTIONS = 0x00000400

STB_GLOBAL = 1
STB_WEAK = 2
STT_OBJECT = 1
STT_FUNC = 2


@dataclass
class TrieNode:
    terminal: bytes = b""
    children: dict[str, "TrieNode"] = field(default_factory=dict)
    offset: int = 0


def _uleb128(value: int) -> bytes:
    if value < 0:
        raise ValueError("ULEB128 requires non-negative value")
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            b |= 0x80
        out.append(b)
        if not value:
            return bytes(out)


def _build_export_trie(exports: dict[str, int]) -> bytes:
    """Build a character-edge Mach-O export trie.

    Mach-O C exports include the leading underscore. Values are offsets from
    the dylib image base, not absolute virtual addresses.
    """
    root = TrieNode()

    for name, address in sorted(exports.items()):
        node = root
        for ch in name:
            node = node.children.setdefault(ch, TrieNode())
        payload = _uleb128(0) + _uleb128(address)
        node.terminal = payload

    nodes: list[TrieNode] = []

    def visit(node: TrieNode) -> None:
        nodes.append(node)
        for child in node.children.values():
            visit(child)

    visit(root)

    def encode_node(node: TrieNode) -> bytes:
        body = bytearray()
        body += _uleb128(len(node.terminal))
        body += node.terminal
        if len(node.children) > 255:
            raise Phase3Error("export trie node has more than 255 children")
        body.append(len(node.children))
        for edge, child in sorted(node.children.items()):
            body += edge.encode("utf-8") + b"\0"
            body += _uleb128(child.offset)
        return bytes(body)

    # Child offsets are ULEB values whose encoded width depends on the final
    # layout, so converge to a fixed point.
    for _ in range(32):
        cursor = 0
        changed = False
        for node in nodes:
            if node.offset != cursor:
                node.offset = cursor
                changed = True
            cursor += len(encode_node(node))
        if not changed:
            break
    else:
        raise Phase3Error("export trie layout failed to converge")

    out = bytearray()
    for node in nodes:
        out += encode_node(node)
    return bytes(out)


def _section_align(addralign: int) -> int:
    if addralign <= 1:
        return 0
    if addralign & (addralign - 1):
        return 0
    return int(math.log2(addralign))


def _section_command(
    *,
    sectname: str,
    segname: str,
    addr: int,
    size: int,
    offset: int,
    align: int,
    flags: int,
) -> bytes:
    return struct.pack(
        "<16s16sQQIIIIIIII",
        sectname.encode("ascii")[:16].ljust(16, b"\0"),
        segname.encode("ascii")[:16].ljust(16, b"\0"),
        addr,
        size,
        offset,
        align,
        0,
        0,
        flags,
        0,
        0,
        0,
    )


def _segment_with_sections(
    seg: ImageSegment,
    sections: list[bytes],
) -> bytes:
    seg_name = seg.name.encode("ascii")[:16].ljust(16, b"\0")
    sentinel = _section_command(
        sectname="__segstart",
        segname=seg.name,
        addr=seg.vmaddr,
        size=0,
        offset=seg.fileoff,
        align=0,
        flags=0,
    )
    all_sections = [sentinel] + sections
    cmdsize = 72 + 80 * len(all_sections)
    header = struct.pack(
        "<II16sQQQQiiII",
        LC_SEGMENT_64,
        cmdsize,
        seg_name,
        seg.vmaddr,
        seg.vmsize,
        seg.fileoff,
        seg.filesize,
        seg.protection,
        seg.protection,
        len(all_sections),
        0,
    )
    return header + b"".join(all_sections)


class Phase4Translator(Phase3Translator):
    """Add dylib exports plus constructor/unwind section metadata to Phase 3."""

    SECTION_MAP = {
        ".text": ("__text", S_ATTR_PURE_INSTRUCTIONS | S_ATTR_SOME_INSTRUCTIONS),
        ".rodata": ("__const", 0),
        ".eh_frame": ("__eh_frame", 0),
        ".gcc_except_table": ("__gcc_except_tab", 0),
        ".init_array": ("__mod_init_func", S_MOD_INIT_FUNC_POINTERS),
        ".fini_array": ("__mod_term_func", S_MOD_TERM_FUNC_POINTERS),
    }

    def _exports(self) -> dict[str, int]:
        exports: dict[str, int] = {}
        for symbol in self.elf.dynamic_symbols():
            if symbol["shndx"] == SHN_UNDEF or not symbol["name"]:
                continue
            binding = symbol["info"] >> 4
            st_type = symbol["info"] & 0x0F
            if binding not in (STB_GLOBAL, STB_WEAK):
                continue
            if st_type not in (STT_FUNC, STT_OBJECT):
                continue
            # Mach-O C symbol spelling uses one leading underscore.
            exports["_" + symbol["name"]] = (
                self.header_reserve + symbol["value"]
            )
        return exports

    def _special_sections(self, seg: ImageSegment) -> list[bytes]:
        result: list[bytes] = []
        for section in self.elf.sections:
            mapping = self.SECTION_MAP.get(section.name)
            if mapping is None or section.size == 0:
                continue
            if not (
                seg.elf_vaddr
                <= section.addr
                < seg.elf_vaddr + seg.elf_filesz
            ):
                continue

            try:
                _segment_index, output_offset, _segment_offset = self._location(
                    section.addr
                )
            except Phase3Error:
                continue

            sectname, flags = mapping
            result.append(
                _section_command(
                    sectname=sectname,
                    segname=seg.name,
                    addr=self.slide + section.addr,
                    size=section.size,
                    offset=output_offset,
                    align=_section_align(section.addralign),
                    flags=flags,
                )
            )
        return result

    def translate(
        self,
        output: str | Path,
        *,
        shim_install_name: str = "@rpath/libbionic_shim.dylib",
        install_name: str = "@rpath/libil2cpp_ported.dylib",
        shim_prefix: str = "_a2i_",
        min_ios: tuple[int, int, int] = (12, 0, 0),
    ) -> dict:
        output = Path(output)

        with tempfile.NamedTemporaryFile(suffix=".dylib", delete=False) as tmp:
            phase3_path = Path(tmp.name)

        try:
            report = super().translate(
                phase3_path,
                shim_install_name=shim_install_name,
                install_name=install_name,
                shim_prefix=shim_prefix,
                min_ios=min_ios,
            )
            data = bytearray(phase3_path.read_bytes())
        finally:
            try:
                phase3_path.unlink()
            except FileNotFoundError:
                pass

        rebase_off = int(report["linkedit"]["fileoff"], 16)
        bind_off = _align(
            rebase_off + report["rebase_stream_size"],
            8,
        )
        bind_end = bind_off + report["bind_stream_size"]

        exports = self._exports()
        export_stream = _build_export_trie(exports)
        export_off = _align(bind_end, 8)
        linkedit_fileoff = rebase_off
        linkedit_size = export_off - linkedit_fileoff + len(export_stream)

        final_size = _align(
            linkedit_fileoff + linkedit_size,
            self.page_size,
        )
        if len(data) < final_size:
            data.extend(b"\0" * (final_size - len(data)))
        data[export_off:export_off + len(export_stream)] = export_stream

        linkedit_vmaddr = int(report["linkedit"]["vmaddr"], 16)

        commands: list[bytes] = []
        for seg in self.segments:
            special = self._special_sections(seg)
            if special:
                commands.append(
                    _segment_with_sections(seg, special)
                )
            else:
                # Keep Phase 3's real file-backed section for segments that do
                # not have named Phase 4 sections.  dyld/llvm validate classic
                # bind offsets against section ranges, so a zero-sized
                # __segstart sentinel alone is not sufficient.
                from .phase3 import _segment_with_data_section
                commands.append(_segment_with_data_section(seg))

        commands.append(
            _segment_no_sections(
                "__LINKEDIT",
                linkedit_vmaddr,
                _align(linkedit_size, self.page_size),
                linkedit_fileoff,
                linkedit_size,
                VM_PROT_READ,
            )
        )
        commands.append(_dylib_command(LC_LOAD_DYLIB, shim_install_name))
        commands.append(_dylib_command(LC_ID_DYLIB, install_name))
        commands.append(_build_version_command(min_ios))
        commands.append(
            struct.pack(
                "<IIIIIIIIIIII",
                LC_DYLD_INFO_ONLY,
                48,
                rebase_off,
                report["rebase_stream_size"],
                bind_off,
                report["bind_stream_size"],
                0,
                0,
                0,
                0,
                export_off,
                len(export_stream),
            )
        )

        load_commands = b"".join(commands)
        header = struct.pack(
            "<IiiIIIII",
            MH_MAGIC_64,
            CPU_TYPE_ARM64,
            CPU_SUBTYPE_ARM64_ALL,
            MH_DYLIB,
            len(commands),
            len(load_commands),
            MH_DYLDLINK | MH_TWOLEVEL,
            0,
        )
        if len(header) + len(load_commands) > self.header_reserve:
            raise Phase3Error("Phase 4 load commands exceed header reserve")

        data[:self.header_reserve] = b"\0" * self.header_reserve
        data[:len(header)] = header
        data[len(header):len(header) + len(load_commands)] = load_commands

        output.write_bytes(data)

        init = self.elf.section(".init_array")
        fini = self.elf.section(".fini_array")
        eh = self.elf.section(".eh_frame")

        report = dict(report)
        report.update({
            "output": str(output),
            "output_size": len(data),
            "export_count": len(exports),
            "export_stream_size": len(export_stream),
            "export_offset": hex(export_off),
            "has_il2cpp_init": "_il2cpp_init" in exports,
            "init_array_entries": 0 if init is None else init.size // 8,
            "fini_array_entries": 0 if fini is None else fini.size // 8,
            "eh_frame_size": 0 if eh is None else eh.size,
            "phase": 4,
            "note": (
                "Phase 4 has Mach-O exports, dyld constructors, and unwind "
                "section metadata. It still requires a semantically sufficient "
                "Bionic shim and does not yet provide the Unity Android player."
            ),
        })
        return report


def translate_phase4(
    input_elf: str | Path,
    output: str | Path,
    **kwargs,
) -> dict:
    from .elf import Elf64
    return Phase4Translator(
        Elf64.from_path(input_elf)
    ).translate(output, **kwargs)
