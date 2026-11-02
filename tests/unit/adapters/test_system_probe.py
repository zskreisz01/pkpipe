"""System probe adapter (docs/operations.md "System preflight"): parsers on tool outputs recorded on the
reference Mac mini (M4, 32 GB, macOS 27, LM Studio with model and judge loaded; 2026-11-02) in
tests/fixtures/system, with application names anonymized in ``macos_ps.txt``. The throttled ``pmset`` output
and the ``linux_*`` files are synthetic, in the real tools' format. Also the macOS probe over a scripted
command runner and the LLM server part over scripted HTTP (LM Studio app or llmster daemon, a non-LM Studio
server, a brief outage)."""

from __future__ import annotations

import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

from pkpipe.adapters.system_probe import (
    LinuxSystemProbe,
    MacOSSystemProbe,
    clean_process_name,
    parse_lms_ps,
    parse_lmstudio_models,
    parse_loadavg,
    parse_meminfo,
    parse_openai_models,
    parse_pmset_therm,
    parse_ps,
    parse_swapusage,
    parse_sysctl,
    parse_vm_stat,
    probe_llm_server,
)
from pkpipe.domain.system import Status, decide

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "system"
URL = "http://127.0.0.1:1234/v1"
MODEL, JUDGE = "qwen3.5-9b-mlx", "google/gemma-4-12b"


def text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_sysctl_swap_and_load_of_the_reference_machine():
    values = parse_sysctl(text("macos_sysctl.txt"))
    assert values["hw.memsize"] == "34359738368" and values["hw.ncpu"] == "10"
    assert values["kern.memorystatus_vm_pressure_level"] == "1" and values["kern.memorystatus_level"] == "34"
    swap = parse_swapusage(values["vm.swapusage"])
    assert swap.total_bytes == 20480 * 1024**2 and swap.used_bytes == int(19044.25 * 1024**2)
    assert swap.used_pct == 93.0
    assert parse_loadavg(values["vm.loadavg"]) == (1.43, 3.02, 5.52)
    assert parse_loadavg(text("linux_loadavg.txt")) == (0.52, 0.58, 0.59)


def test_vm_stat_pages():
    vm = parse_vm_stat(text("macos_vm_stat.txt"))
    assert vm["page_size"] == 16384
    assert vm["Pages free"] > 0 and vm["Pages occupied by compressor"] > 0 and "Translation faults" in vm


def test_pmset_therm_with_and_without_throttling():
    calm = parse_pmset_therm(text("macos_pmset_therm.txt"))
    assert calm.cpu_speed_limit is None and calm.warning is False and not calm.throttled
    hot = parse_pmset_therm(text("macos_pmset_therm_throttled.txt"))
    assert hot.cpu_speed_limit == 70 and hot.warning and hot.throttled


def test_ps_keeps_names_only_summed_and_ranked():
    procs = parse_ps(text("macos_ps.txt"), top=4)
    assert [p.name for p in procs] == [
        "node",
        "agent-cli",
        "com.apple.Virtualization.VirtualMachine",
        "Editor Helper (Renderer)",
    ]
    assert procs[0].rss_bytes == (1082432 + 759952) * 1024  # two node processes
    assert procs[1].rss_bytes == (529184 + 212208 + 168048) * 1024


def test_process_names_never_carry_paths_or_control_characters():
    assert clean_process_name("/Users/someone/private/tool") == "tool"  # ps -c / comm: no arguments
    assert "/" not in clean_process_name("/Applications/App.app/Contents/MacOS/App")
    assert clean_process_name("bad\x1bname\n") == "badname"
    assert len(clean_process_name("x" * 200)) == 48
    rows = parse_ps("  1024 /Users/someone/bin/secret-thing\n2048 node\nnot a row\n")
    assert [p.name for p in rows] == ["node", "secret-thing"]


def test_linux_meminfo():
    mem = parse_meminfo(text("linux_meminfo.txt"))
    assert mem["MemTotal"] == 16303428 * 1024 and mem["MemAvailable"] == 6123344 * 1024


def test_lm_studio_payloads():
    assert parse_openai_models(json.loads(text("openai_models.json")))[:2] == (MODEL, JUDGE)
    rest = parse_lmstudio_models(json.loads(text("lmstudio_v0_models.json")))
    assert rest[MODEL] == {"loaded": True, "context_length": 32768}
    assert rest["qwen3-4b"]["loaded"] is False
    ps = parse_lms_ps(json.loads(text("lms_ps.json")))
    assert ps[MODEL]["parallel"] == 4 and ps[JUDGE]["context_length"] == 32768
    assert {v["status"] for v in ps.values()} <= {"idle", "generating", "processingPrompt", "loading"}
    assert parse_lms_ps({"not": "a list"}) == {}


class Runner:
    """Scripted command runner: argv[0] -> fixture text (or an exception)."""

    def __init__(self, **outputs):
        self.outputs = {"sysctl": "macos_sysctl.txt", "vm_stat": "macos_vm_stat.txt"} | outputs
        self.outputs.setdefault("pmset", "macos_pmset_therm.txt")
        self.outputs.setdefault("ps", "macos_ps.txt")
        self.outputs.setdefault("lms", "lms_ps.json")
        self.calls: list[list[str]] = []

    def __call__(self, cmd, timeout):
        self.calls.append(list(cmd))
        out = self.outputs[Path(cmd[0]).name]
        if isinstance(out, Exception):
            raise out
        return text(out)


class Http:
    def __init__(self, routes: dict[str, object], failures: int = 0):
        self.routes, self.failures, self.calls = routes, failures, []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if self.failures:
            self.failures -= 1
            raise urllib.error.URLError("connection refused")
        out = self.routes.get(url.removeprefix("http://127.0.0.1:1234"))
        if out is None:
            raise urllib.error.HTTPError(url, 404, "not found", None, None)
        return json.loads(text(out)) if isinstance(out, str) else out


LMSTUDIO = {"/v1/models": "openai_models.json", "/api/v0/models": "lmstudio_v0_models.json"}


def test_macos_probe_on_recorded_outputs_and_the_resulting_decision():
    run = Runner()
    probe = MacOSSystemProbe(URL, run=run, http_get=Http(LMSTUDIO), lms="/opt/lms", thermal=lambda: "nominal")
    snap = probe.snapshot()
    assert snap.cpu_count == 10 and snap.memory.total_bytes == 32 * 1024**3
    assert snap.memory.pressure == "normal" and snap.memory.available_pct == 34.0
    assert snap.memory.compressed_bytes and snap.memory.free_bytes
    assert snap.swap.used_pct == 93.0 and snap.thermal.level == "nominal" and snap.probe_errors == ()
    assert snap.processes[0].name == "node" and len(snap.processes) == 5
    assert snap.llm.reachable and snap.llm.sources == ("openai", "lmstudio-rest", "lms")
    qwen = snap.llm.model(MODEL)
    assert (qwen.loaded, qwen.parallel, qwen.context_length) == (True, 4, 32768)
    assert snap.llm.model("qwen3-4b").loaded is False
    assert ["ps", "-axco", "rss=,comm="] in run.calls and ["/opt/lms", "ps", "--json"] in run.calls
    decision = decide(snap, required_models=(MODEL, JUDGE))
    assert decision.status is Status.DEGRADED and decision.concurrency == 2  # swap 93% on the reference run
    assert snap.to_dict()["llm"]["models"][0]["id"] == MODEL


def test_a_failing_tool_is_reported_not_raised():
    run = Runner(pmset=FileNotFoundError("pmset"), ps=subprocess.TimeoutExpired("ps", 5))
    probe = MacOSSystemProbe(None, run=run, thermal=lambda: (_ for _ in ()).throw(OSError("no objc")))
    snap = probe.snapshot()
    assert set(snap.probe_errors) == {
        "pmset: FileNotFoundError",
        "ps: TimeoutExpired",
        "thermalState: OSError",
    }
    assert snap.thermal.level is None and snap.processes == () and snap.llm is None
    assert decide(snap).status is Status.DEGRADED  # still decided on what was read (swap 93%)


def test_include_llm_false_never_contacts_the_server():
    http = Http(LMSTUDIO)
    snap = MacOSSystemProbe(URL, run=Runner(), http_get=http, thermal=lambda: "fair").snapshot(
        include_llm=False
    )
    assert snap.llm is None and http.calls == []


def test_brief_unavailability_is_retried_and_a_down_server_is_unreachable():
    """LM Studio switching from the app to the llmster daemon (or restarting) drops the port for a moment."""
    sleeps: list[float] = []
    http = Http(LMSTUDIO, failures=2)
    state = probe_llm_server(URL, http_get=http, run=Runner(), lms="/opt/lms", retries=2, sleep=sleeps.append)
    assert state.reachable and sleeps == [2.0, 2.0] and state.model(MODEL).parallel == 4
    down = probe_llm_server(URL, http_get=Http(LMSTUDIO, failures=9), retries=2, sleep=sleeps.append)
    assert not down.reachable and down.error == "URLError" and down.models == ()


def test_daemon_without_rest_api_uses_lms_and_marks_the_rest_not_loaded():
    """A server that answers /v1/models but not /api/v0 (an older daemon) with an explicit lms CLI."""
    state = probe_llm_server(
        URL, http_get=Http({"/v1/models": "openai_models.json"}), run=Runner(), lms="/x/lms"
    )
    assert state.sources == ("openai", "lms")
    assert state.model(MODEL).loaded is True and state.model("qwen3-4b").loaded is False


def test_non_lm_studio_server_reports_only_what_it_serves():
    payload = {"data": [{"id": "llama3"}]}
    state = probe_llm_server(
        "http://127.0.0.1:11434/v1",
        http_get=lambda url, t: (
            payload if url.endswith("/v1/models") else (_ for _ in ()).throw(ValueError())
        ),
        run=Runner(),
    )
    assert state.sources == ("openai",) and state.model("llama3").loaded is None


def test_lms_failure_is_a_probe_error_and_a_remote_server_never_runs_lms():
    errors: list[str] = []
    run = Runner(lms=subprocess.CalledProcessError(1, "lms"))
    state = probe_llm_server(URL, http_get=Http(LMSTUDIO), run=run, lms="/x/lms", errors=errors)
    assert (
        state.reachable and errors == ["lms ps: CalledProcessError"] and state.model(MODEL).parallel is None
    )
    remote = Runner()
    probe_llm_server(
        "http://10.0.0.5:1234/v1",
        http_get=lambda url, t: json.loads(text("openai_models.json")),
        run=remote,
        lms="/x/lms",
    )
    assert remote.calls == []


def test_linux_probe_reads_proc(tmp_path):
    (tmp_path / "meminfo").write_text(text("linux_meminfo.txt"))
    (tmp_path / "loadavg").write_text(text("linux_loadavg.txt"))
    probe = LinuxSystemProbe(None, proc=tmp_path, run=lambda cmd, t: "2048 python3\n1024 bash\n")
    snap = probe.snapshot()
    assert snap.memory.available_pct == pytest.approx(37.6, abs=0.1) and snap.memory.pressure is None
    assert snap.swap.used_bytes == (4194300 - 3145724) * 1024 and snap.load_avg == (0.52, 0.58, 0.59)
    assert [p.name for p in snap.processes] == ["python3", "bash"] and snap.platform.startswith("linux")
