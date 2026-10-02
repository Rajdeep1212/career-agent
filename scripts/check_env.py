"""Hardware preflight for local models (docs/ROADMAP_QUEUE.md SEM0).

Usage (from the repository root, in the app's Python environment):
    python scripts/check_env.py

Read-only and offline: it measures this machine, prints PASS or FAIL per requirement with the
measured number, says where the Hugging Face model cache should live, and writes
docs/eval/env_check.md. It installs nothing, downloads nothing, changes no setting and removes no
file. A GPU is not required: the planned models run on the CPU.
"""
import argparse
import ctypes
import importlib.metadata
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GB = 1024 ** 3
RAM_TOTAL_GB, RAM_FREE_GB, CACHE_FREE_GB, REPO_FREE_GB = 8, 3, 8, 2
PACKAGES = ("torch", "sentence-transformers", "huggingface_hub")
_KINDS = {2: "removable", 3: "fixed", 4: "network", 5: "optical", 6: "ramdisk"}


@dataclass(frozen=True)
class Drive:
    letter: str
    kind: str               # fixed | removable | network | optical | ramdisk | unknown
    bus: str | None         # NVMe, SATA, USB, ...; None when Windows did not say
    total: int
    free: int

    @property
    def internal(self) -> bool:
        return self.kind == "fixed" and (self.bus or "").upper() != "USB"


@dataclass(frozen=True)
class Measured:
    cpu_cores: int
    ram_total: int
    ram_free: int
    gpu: str
    drives: list[Drive]
    repo_drive: str
    cache_path: Path
    cache_size: int


@dataclass(frozen=True)
class Line:
    name: str
    measured: str
    requirement: str
    verdict: str            # PASS | FAIL


@dataclass(frozen=True)
class CacheDecision:
    action: str             # keep | move | stop
    drive: str | None
    path: Path | None
    reason: str


def gb(value: int) -> str:
    return f"{value / GB:.1f} GB"


def hf_cache_path(environ, home: Path) -> Path:
    """Where huggingface_hub keeps its cache: HF_HOME, else XDG_CACHE_HOME/huggingface, else ~/.cache/huggingface."""
    if environ.get("HF_HOME"):
        return Path(environ["HF_HOME"])
    if environ.get("XDG_CACHE_HOME"):
        return Path(environ["XDG_CACHE_HOME"]) / "huggingface"
    return home / ".cache" / "huggingface"


def _drive_of(measured: Measured, letter: str) -> Drive | None:
    return next((drive for drive in measured.drives if drive.letter == letter.upper()), None)


def verdicts(measured: Measured) -> list[Line]:
    def line(name: str, value: int | None, need_gb: int, label: str = "") -> Line:
        shown = "not found" if value is None else f"{gb(value)}{label}"
        return Line(name, shown, f">= {need_gb} GB", "PASS" if value is not None and value >= need_gb * GB else "FAIL")

    cache_letter = measured.cache_path.drive[:1].upper()
    cache, repo = _drive_of(measured, cache_letter), _drive_of(measured, measured.repo_drive)
    return [line("RAM total", measured.ram_total, RAM_TOTAL_GB), line("RAM free", measured.ram_free, RAM_FREE_GB),
            line("Cache drive free", cache.free if cache else None, CACHE_FREE_GB, f" on {cache_letter}:"),
            line("Repo drive free", repo.free if repo else None, REPO_FREE_GB, f" on {measured.repo_drive}:"),
            Line("GPU", measured.gpu, "not required (the models run on the CPU)", "PASS")]


def decide_cache(measured: Measured) -> CacheDecision:
    """Keep the cache when its drive has room; else the internal drive with the most room, then an external one; never a network drive."""
    need = CACHE_FREE_GB * GB
    letter = measured.cache_path.drive[:1].upper()
    current = _drive_of(measured, letter)
    if current and current.free >= need:
        return CacheDecision("keep", letter, measured.cache_path, f"{letter}: has {gb(current.free)} free, at least {CACHE_FREE_GB} GB")
    roomy = [drive for drive in measured.drives if drive.kind in ("fixed", "removable") and drive.free >= need]
    if not roomy:
        return CacheDecision("stop", None, None, f"no local drive has {CACHE_FREE_GB} GB free")
    best = max(roomy, key=lambda drive: (drive.internal, drive.free))
    where = "internal" if best.internal else "external or removable"
    short = f"{letter}: has only {gb(current.free)} free" if current else f"{letter}: was not found"
    return CacheDecision("move", best.letter, Path(f"{best.letter}:/ai-cache/huggingface"),
                         f"{short}; {best.letter}: is the {where} drive with the most room, {gb(best.free)} free")


def _bus_types() -> dict[str, str]:
    """Drive letter -> bus type (NVMe, SATA, USB) from Windows; empty when it cannot be read."""
    command = ("Get-Partition | Where-Object DriveLetter | ForEach-Object { "
               "\"$($_.DriveLetter)=$((Get-Disk -Number $_.DiskNumber).BusType)\" }")
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True,
                              timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    return dict(line.strip().split("=", 1) for line in done.stdout.splitlines() if "=" in line)


def _drives() -> list[Drive]:
    if os.name != "nt":
        usage = shutil.disk_usage("/")
        return [Drive("/", "fixed", None, usage.total, usage.free)]
    kernel = ctypes.windll.kernel32
    mask, buses, found = kernel.GetLogicalDrives(), _bus_types(), []
    for index in range(26):
        if not mask >> index & 1:
            continue
        letter = chr(ord("A") + index)
        kind = _KINDS.get(kernel.GetDriveTypeW(f"{letter}:\\"), "unknown")
        try:
            usage = shutil.disk_usage(f"{letter}:\\")
        except OSError:
            continue        # an empty card reader or an unreachable share
        found.append(Drive(letter, kind, buses.get(letter), usage.total, usage.free))
    return found


def _ram() -> tuple[int, int]:
    if os.name == "nt":
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("available", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                        ("avail_extended", ctypes.c_ulonglong)]
        status = Status()
        status.length = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.total, status.available
    page = os.sysconf("SC_PAGE_SIZE")
    return page * os.sysconf("SC_PHYS_PAGES"), page * os.sysconf("SC_AVPHYS_PAGES")


def _gpu() -> str:
    try:
        import torch
    except Exception:
        return "none (torch is not installed, so CUDA was not checked)"
    if not torch.cuda.is_available():
        return "none"
    return f"{torch.cuda.get_device_name(0)} ({gb(torch.cuda.get_device_properties(0).total_memory)})"


def _size(path: Path) -> int:
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total


def packages() -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for name in PACKAGES:
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            found[name] = None
    return found


def measure() -> Measured:
    total, free = _ram()
    cache = hf_cache_path(os.environ, Path.home())
    return Measured(cpu_cores=os.cpu_count() or 0, ram_total=total, ram_free=free, gpu=_gpu(), drives=_drives(),
                    repo_drive=(ROOT.drive[:1] or "/").upper(), cache_path=cache, cache_size=_size(cache) if cache.exists() else 0)


def other_caches() -> dict[str, tuple[Path, int]]:
    """Caches that can be cleared by hand, with sizes; shown only when no drive has room. Nothing is removed here."""
    local, home = Path(os.environ.get("LOCALAPPDATA", "")), Path.home()
    places = {"pip": local / "pip" / "Cache", "npm": local / "npm-cache", "conda packages": Path(sys.base_prefix).parents[1] / "pkgs"
              if "envs" in Path(sys.base_prefix).parts else Path(sys.base_prefix) / "pkgs",
              "Hugging Face": hf_cache_path(os.environ, home)}
    return {name: (path, _size(path)) for name, path in places.items() if path.exists()}


def _table(header: list[str], rows) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(str(cell) for cell in row) + " |"
                                                                             for row in rows] + [""]


def render(measured: Measured, lines: list[Line], decision: CacheDecision, *, packages: dict[str, str | None], python: str,
           conda_env: str, checked_at: str) -> str:
    overall = "PASS" if all(line.verdict == "PASS" for line in lines) else "FAIL"
    text = ["# Environment check for local models (SEM0)", "",
            f"Measured by `python scripts/check_env.py` on {checked_at}. Read-only and offline; re-run it to refresh this file.", "",
            "## Requirements", ""]
    text += _table(["Requirement", "Measured", "Needed", "Verdict"], [[line.name, line.measured, line.requirement, line.verdict]
                                                                      for line in lines])
    text += [f"Overall: {overall}. A GPU is not required.", "",
             f"Cache placement: {decision.action}" + (f" ({decision.path})" if decision.path else "") + f". {decision.reason}.", "",
             "## Machine", ""]
    text += _table(["Item", "Value"], [["CPU cores", measured.cpu_cores], ["RAM total", gb(measured.ram_total)],
                                       ["RAM free", gb(measured.ram_free)], ["GPU (CUDA seen by torch)", measured.gpu],
                                       ["Python", python], ["Conda env", conda_env],
                                       ["Hugging Face cache", f"{measured.cache_path} ({gb(measured.cache_size)})"]])
    text += ["## Drives", ""]
    text += _table(["Drive", "Type", "Bus", "Total", "Free"], [[f"{drive.letter}:", drive.kind, drive.bus or "not reported", gb(drive.total),
                                                                gb(drive.free)] for drive in measured.drives])
    text += ["## Packages", ""]
    text += _table(["Package", "Version"], [[name, version or "not installed"] for name, version in packages.items()])
    return "\n".join(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure this machine for local models. Read-only and offline.")
    parser.add_argument("--report", type=Path, default=ROOT / "docs" / "eval" / "env_check.md")
    args = parser.parse_args(argv)
    measured = measure()
    lines, decision, found = verdicts(measured), decide_cache(measured), packages()
    conda_env = os.environ.get("CONDA_DEFAULT_ENV") or Path(sys.prefix).name
    report = render(measured, lines, decision, packages=found, python=sys.version.split()[0], conda_env=conda_env,
                    checked_at=datetime.now().strftime("%Y-%m-%d %H:%M"))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report + "\n", encoding="utf-8")
    for line in lines:
        print(f"{line.verdict}  {line.name}: {line.measured} (needed: {line.requirement})")
    print(f"cache: {decision.action}" + (f" -> {decision.path}" if decision.path else "") + f" ({decision.reason})")
    if decision.action == "stop":
        for drive in measured.drives:
            print(f"  {drive.letter}: {drive.kind}, {gb(drive.free)} free")
        for name, (path, size) in other_caches().items():
            print(f"  {name} cache: {gb(size)} at {path}")
    print(f"report: {args.report}")
    return 0 if all(line.verdict == "PASS" for line in lines) and decision.action != "stop" else 1


if __name__ == "__main__":
    raise SystemExit(main())
