"""SystemProbePort adapters: macOS (sysctl, vm_stat, pmset, ps, NSProcessInfo) and a minimal Linux one.

Everything runs without sudo and in well under a second: one ``sysctl`` call for memory size, CPU count,
load, swap and the kernel's memory pressure level and free percentage; ``vm_stat`` for free and compressed
pages; ``pmset -g therm`` for a CPU speed limit / thermal warning; ``NSProcessInfo.thermalState`` through the
Objective-C runtime (ctypes, no extra dependency); ``ps`` for the top memory consumers by executable *name*
(``-c``: no paths, no arguments). The LLM server part asks the OpenAI-compatible ``/v1/models``, LM Studio's
REST ``/api/v0/models`` (loaded state, context) and ``lms ps --json`` (parallel, queued requests) when the
server is local; it works the same for the LM Studio app and the headless ``llmster`` daemon, and retries a
briefly unavailable server. A reading that fails is recorded in ``probe_errors`` and left empty.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pkpipe.domain.system import (
    THERMAL_LEVELS,
    LlmModel,
    LlmServerState,
    MemoryState,
    ProcessUsage,
    SwapState,
    SystemSnapshot,
    ThermalState,
)

#: ``cmd, timeout_s -> stdout``; raises ``OSError`` / ``subprocess.SubprocessError`` on failure.
Runner = Callable[[Sequence[str], float], str]
#: ``url, timeout_s -> parsed JSON``; raises ``OSError`` / ``ValueError`` on failure.
HttpGet = Callable[[str, float], Any]

SYSCTL_KEYS = (
    "hw.memsize",
    "hw.ncpu",
    "vm.loadavg",
    "vm.swapusage",
    "kern.memorystatus_vm_pressure_level",
    "kern.memorystatus_level",
)
_PRESSURE = {1: "normal", 2: "warn", 4: "critical"}
_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MAX_NAME = 48


def run_command(cmd: Sequence[str], timeout: float) -> str:
    out = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, check=True)
    return out.stdout


def http_get_json(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - configured LLM server URL
        return json.load(resp)


# -- parsers (pure; tested on recorded outputs in tests/fixtures/system) ------------------------------------
def _size(text: str) -> int:
    m = re.fullmatch(r"\s*([\d.]+)\s*([KMGT])?B?\s*", text)
    if not m:
        raise ValueError(f"not a size: {text!r}")
    return int(float(m.group(1)) * _UNITS.get(m.group(2) or "", 1))


def parse_sysctl(text: str) -> dict[str, str]:
    """``key: value`` lines of ``sysctl <keys>``."""
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


def parse_swapusage(value: str) -> SwapState:
    """``total = 19456.00M  used = 17978.75M  free = 1477.25M  (encrypted)``."""
    fields = dict(re.findall(r"(\w+)\s*=\s*([\d.]+[KMGT]?)", value))
    return SwapState(total_bytes=_size(fields["total"]), used_bytes=_size(fields["used"]))


def parse_loadavg(value: str) -> tuple[float, float, float]:
    """``{ 2.71 10.65 9.13 }`` (sysctl) or ``0.52 0.58 0.59 1/389 12345`` (/proc/loadavg)."""
    nums = re.findall(r"\d+(?:\.\d+)?", value.replace("{", " "))
    return float(nums[0]), float(nums[1]), float(nums[2])


def parse_vm_stat(text: str) -> dict[str, int]:
    """Page counts by name plus ``page_size`` from the header."""
    out: dict[str, int] = {}
    m = re.search(r"page size of (\d+) bytes", text)
    if m:
        out["page_size"] = int(m.group(1))
    for name, value in re.findall(r'^"?([^":\n]+)"?:\s+(\d+)\.?\s*$', text, re.MULTILINE):
        out[name.strip()] = int(value)
    return out


def parse_pmset_therm(text: str) -> ThermalState:
    """``pmset -g therm``: ``CPU_Speed_Limit = 80`` when throttled; a thermal warning level line when one was
    recorded (``Note: No thermal warning level has been recorded`` otherwise)."""
    m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", text)
    warning = any(
        "thermal warning level" in line.lower() and "no thermal warning" not in line.lower()
        for line in text.splitlines()
    )
    return ThermalState(cpu_speed_limit=int(m.group(1)) if m else None, warning=warning)


def clean_process_name(name: str) -> str:
    """The executable name only: no directory, no arguments, printable, at most 48 characters."""
    name = name.strip().split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    return name[:MAX_NAME] or "?"


def parse_ps(text: str, top: int = 5) -> tuple[ProcessUsage, ...]:
    """``ps -axco rss=,comm=`` (KiB, name): same-named processes summed, largest ``top`` first."""
    totals: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        name = clean_process_name(parts[1])
        totals[name] = totals.get(name, 0) + int(parts[0]) * 1024
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return tuple(ProcessUsage(n, b) for n, b in ranked)


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo in bytes."""
    out = {}
    for name, value, unit in re.findall(r"^(\w+):\s+(\d+)\s*(kB)?", text, re.MULTILINE):
        out[name] = int(value) * (1024 if unit else 1)
    return out


def parse_openai_models(payload: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(m["id"]) for m in payload.get("data", []) if isinstance(m, Mapping) and "id" in m)


def parse_lmstudio_models(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """LM Studio REST ``/api/v0/models``: id -> {loaded, context_length}."""
    out = {}
    for m in payload.get("data", []):
        if isinstance(m, Mapping) and "id" in m:
            out[str(m["id"])] = {
                "loaded": m.get("state") == "loaded",
                "context_length": m.get("loaded_context_length"),
            }
    return out


def parse_lms_ps(payload: Any) -> dict[str, dict[str, Any]]:
    """``lms ps --json``: identifier -> {loaded, parallel, context_length, status, queued}."""
    out = {}
    for m in payload if isinstance(payload, list) else []:
        if not isinstance(m, Mapping):
            continue
        ident = m.get("identifier") or m.get("modelKey")
        if not ident:
            continue
        out[str(ident)] = {
            "loaded": True,
            "parallel": m.get("parallel"),
            "context_length": m.get("contextLength"),
            "status": m.get("status"),
            "queued": m.get("queued"),
        }
    return out


# -- LLM server ------------------------------------------------------------------------------------------
def find_lms() -> str | None:
    """The ``lms`` CLI of LM Studio (app or llmster daemon): PATH, ``LMS_BIN`` or ``~/.lmstudio/bin/lms``."""
    candidates = [
        os.environ.get("LMS_BIN"),
        shutil.which("lms"),
        str(Path("~/.lmstudio/bin/lms").expanduser()),
    ]
    return next((c for c in candidates if c and os.access(c, os.X_OK)), None)


def probe_llm_server(
    base_url: str,
    *,
    http_get: HttpGet = http_get_json,
    run: Runner = run_command,
    lms: str | None = None,
    retries: int = 2,
    retry_delay_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    errors: list[str] | None = None,
) -> LlmServerState:
    """What the server serves and, when it says so, what is loaded with which parallel / context / queue."""
    base_url = base_url.rstrip("/")
    errors = errors if errors is not None else []
    available: tuple[str, ...] | None = None
    last_error = ""
    for attempt in range(retries + 1):
        try:
            available = parse_openai_models(http_get(f"{base_url}/models", 3.0))
            break
        except (OSError, ValueError, KeyError, TypeError) as exc:
            last_error = type(exc).__name__
            if attempt < retries:
                sleep(retry_delay_s)
    if available is None:
        return LlmServerState(base_url, False, error=last_error)
    sources = ["openai"]
    info: dict[str, dict[str, Any]] = {m: {} for m in available}
    root = base_url[: -len("/v1")] if base_url.endswith("/v1") else base_url
    try:
        rest = parse_lmstudio_models(http_get(f"{root}/api/v0/models", 3.0))
        sources.append("lmstudio-rest")
        for mid, values in rest.items():
            info.setdefault(mid, {}).update(values)
    except (OSError, ValueError, KeyError, TypeError):
        pass  # not LM Studio (Ollama, vLLM, a cloud endpoint): /v1/models is all there is
    if (urlsplit(base_url).hostname or "") in _LOOPBACK and (lms or "lmstudio-rest" in sources):
        lms = lms or find_lms()
        if lms:
            try:
                ps = parse_lms_ps(json.loads(run([lms, "ps", "--json"], 8.0) or "[]"))
                sources.append("lms")
                if "lmstudio-rest" not in sources:  # lms knows the loaded ones only
                    for values in info.values():
                        values.setdefault("loaded", False)
                for mid, values in ps.items():
                    info.setdefault(mid, {}).update({k: v for k, v in values.items() if v is not None})
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                errors.append(f"lms ps: {type(exc).__name__}")
    models = tuple(
        LlmModel(
            id=mid,
            loaded=v.get("loaded"),
            parallel=v.get("parallel"),
            context_length=v.get("context_length"),
            status=v.get("status"),
            queued=v.get("queued"),
        )
        for mid, v in info.items()
    )
    return LlmServerState(base_url, True, models, tuple(sources))


# -- NSProcessInfo.thermalState ------------------------------------------------------------------------
def macos_thermal_level() -> str | None:
    """``NSProcessInfo.processInfo.thermalState`` (0 nominal .. 3 critical) via the Objective-C runtime."""
    import ctypes
    import ctypes.util

    objc_path, foundation = ctypes.util.find_library("objc"), ctypes.util.find_library("Foundation")
    if not objc_path or not foundation:
        return None
    objc = ctypes.cdll.LoadLibrary(objc_path)
    ctypes.cdll.LoadLibrary(foundation)
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.restype = ctypes.c_void_p
    send_ptr = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
    send_long = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
    cls = objc.objc_getClass(b"NSProcessInfo")
    if not cls:
        return None
    info = send_ptr(cls, objc.sel_registerName(b"processInfo"))
    state = send_long(info, objc.sel_registerName(b"thermalState"))
    return THERMAL_LEVELS[state] if 0 <= state < len(THERMAL_LEVELS) else None


# -- probes ----------------------------------------------------------------------------------------------
class _BaseProbe:
    def __init__(
        self,
        llm_base_url: str | None = None,
        *,
        run: Runner = run_command,
        http_get: HttpGet = http_get_json,
        lms: str | None = None,
        top_processes: int = 5,
        llm_retries: int = 2,
        llm_retry_delay_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.llm_base_url = llm_base_url
        self.run = run
        self.http_get = http_get
        self.lms = lms
        self.top = top_processes
        self.llm_retries = llm_retries
        self.llm_retry_delay_s = llm_retry_delay_s
        self.sleep = sleep
        self.clock = clock

    def _try(self, label: str, fn: Callable[[], Any], errors: list[str]) -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - a missing reading is reported, never raised
            errors.append(f"{label}: {type(exc).__name__}")
            return None

    def _llm(self, include_llm: bool, errors: list[str]) -> LlmServerState | None:
        if not include_llm or not self.llm_base_url:
            return None
        return probe_llm_server(
            self.llm_base_url,
            http_get=self.http_get,
            run=self.run,
            lms=self.lms,
            retries=self.llm_retries,
            retry_delay_s=self.llm_retry_delay_s,
            sleep=self.sleep,
            errors=errors,
        )

    def _processes(self, cmd: Sequence[str], errors: list[str]) -> tuple[ProcessUsage, ...]:
        return self._try("ps", lambda: parse_ps(self.run(cmd, 5.0), self.top), errors) or ()


class MacOSSystemProbe(_BaseProbe):
    """Implements :class:`pkpipe.ports.SystemProbePort` on macOS (Apple silicon and Intel)."""

    def __init__(self, *args: Any, thermal: Callable[[], str | None] = macos_thermal_level, **kw: Any):
        super().__init__(*args, **kw)
        self.thermal = thermal

    def snapshot(self, *, include_llm: bool = True) -> SystemSnapshot:
        errors: list[str] = []
        sysctl = (
            self._try("sysctl", lambda: parse_sysctl(self.run(["sysctl", *SYSCTL_KEYS], 5.0)), errors) or {}
        )
        vm = self._try("vm_stat", lambda: parse_vm_stat(self.run(["vm_stat"], 5.0)), errors) or {}
        total = int(sysctl.get("hw.memsize") or 0)
        page = vm.get("page_size", 16384)
        level = sysctl.get("kern.memorystatus_level")
        pressure = sysctl.get("kern.memorystatus_vm_pressure_level")
        memory = MemoryState(
            total_bytes=total,
            available_bytes=int(total * int(level) / 100) if level and level.isdigit() and total else None,
            free_bytes=(vm["Pages free"] + vm.get("Pages speculative", 0)) * page
            if "Pages free" in vm
            else None,
            compressed_bytes=vm["Pages occupied by compressor"] * page
            if "Pages occupied by compressor" in vm
            else None,
            pressure=_PRESSURE.get(int(pressure)) if pressure and pressure.isdigit() else None,
        )
        swap = self._try("swap", lambda: parse_swapusage(sysctl["vm.swapusage"]), errors) or SwapState()
        load = self._try("loadavg", lambda: parse_loadavg(sysctl["vm.loadavg"]), errors)
        therm = self._try("pmset", lambda: parse_pmset_therm(self.run(["pmset", "-g", "therm"], 5.0)), errors)
        level_name = self._try("thermalState", self.thermal, errors)
        thermal = ThermalState(
            level=level_name,
            cpu_speed_limit=therm.cpu_speed_limit if therm else None,
            warning=therm.warning if therm else False,
        )
        cpus = int(sysctl.get("hw.ncpu") or 0) or os.cpu_count() or 1
        processes = self._processes(["ps", "-axco", "rss=,comm="], errors)
        llm = self._llm(include_llm, errors)
        return SystemSnapshot(
            taken_at=self.clock(),
            platform=f"darwin-{platform.machine()}",
            cpu_count=cpus,
            load_avg=load,
            memory=memory,
            swap=swap,
            thermal=thermal,
            processes=processes,
            llm=llm,
            probe_errors=tuple(errors),
        )


class LinuxSystemProbe(_BaseProbe):
    """Minimal :class:`pkpipe.ports.SystemProbePort` for Linux (CI): /proc/meminfo, /proc/loadavg, ps.
    Memory pressure and thermal state are not read."""

    def __init__(self, *args: Any, proc: Path = Path("/proc"), **kw: Any):
        super().__init__(*args, **kw)
        self.proc = proc

    def snapshot(self, *, include_llm: bool = True) -> SystemSnapshot:
        errors: list[str] = []
        mem = self._try("meminfo", lambda: parse_meminfo((self.proc / "meminfo").read_text()), errors) or {}
        load = self._try("loadavg", lambda: parse_loadavg((self.proc / "loadavg").read_text()), errors)
        memory = MemoryState(
            total_bytes=mem.get("MemTotal", 0),
            available_bytes=mem.get("MemAvailable"),
            free_bytes=mem.get("MemFree"),
        )
        swap = SwapState(mem.get("SwapTotal", 0), mem.get("SwapTotal", 0) - mem.get("SwapFree", 0))
        return SystemSnapshot(
            taken_at=self.clock(),
            platform=f"linux-{platform.machine()}",
            cpu_count=os.cpu_count() or 1,
            load_avg=load,
            memory=memory,
            swap=swap,
            processes=self._processes(["ps", "-eo", "rss=,comm="], errors),
            llm=self._llm(include_llm, errors),
            probe_errors=tuple(errors),
        )


def make_system_probe(llm_base_url: str | None = None, **kw: Any) -> MacOSSystemProbe | LinuxSystemProbe:
    """The probe for this host: macOS, else the /proc one (Linux; elsewhere it reports what it cannot
    read)."""
    if sys.platform == "darwin":
        return MacOSSystemProbe(llm_base_url, **kw)
    return LinuxSystemProbe(llm_base_url, **kw)
