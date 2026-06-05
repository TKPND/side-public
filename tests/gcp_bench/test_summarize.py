import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "gcp" / "bench" / "summarize.py"
SPEC = importlib.util.spec_from_file_location("gce_bench_summarize", MODULE_PATH)
summarize = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(summarize)


def result(machine_type, elapsed, exit_code=0, timed_out=False, arch="x86_64"):
    return {
        "machine_type": machine_type,
        "architecture": arch,
        "commands": [
            {
                "name": "cold_debug_build",
                "elapsed_sec": elapsed,
                "exit_code": exit_code,
                "timed_out": timed_out,
            },
            {
                "name": "bench_wfd",
                "elapsed_sec": elapsed / 2,
                "exit_code": exit_code,
                "timed_out": timed_out,
            },
        ],
    }


def test_score_prefers_lowest_weighted_runtime():
    rows = summarize.score_candidates([result("n4-standard-8", 100), result("c4-standard-8", 80)])
    recommendation = summarize.recommend(rows)
    assert recommendation["recommended_vm"] == "c4-standard-8"


def test_timeout_candidate_is_excluded():
    rows = summarize.score_candidates(
        [result("n4-standard-8", 100), result("c4-standard-8", 50, exit_code=124, timed_out=True)]
    )
    excluded = {row["machine_type"]: row for row in rows if row["status"] == "excluded"}
    assert excluded["c4-standard-8"]["reason"] == "timeout"


def test_arm_incompatibility_is_excluded():
    arm = result("c4a-standard-8", 70, arch="aarch64")
    arm["arm_compatible"] = False
    rows = summarize.score_candidates([arm])
    assert rows[0]["status"] == "excluded"
    assert rows[0]["reason"] == "ARM compatibility failure"


def test_final_vm_shape_resizes_eight_vcpu_to_sixteen():
    assert summarize.final_vm_shape("n4-standard-8") == {
        "primary": "n4-standard-8",
        "heavy_job_resize": "n4-standard-16",
        "disk_gb": 300,
    }


def test_skipped_side_scan_does_not_fail_candidate():
    candidate = result("n4-standard-8", 100)
    candidate["commands"].append(
        {
            "name": "side_scan",
            "skipped": True,
            "reason": "SCAN_BENCH_ARGS is not configured",
            "exit_code": None,
            "timed_out": False,
        }
    )
    rows = summarize.score_candidates([candidate])
    assert rows[0]["status"] == "included"
