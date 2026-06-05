#!/usr/bin/env python3
"""
Compare v3.8 baseline vs v3.9 rescan for USDJPY combined.
Computes sha256 hashes and identifies slot-level mismatches.
"""

import json
import hashlib
from pathlib import Path
from typing import Any, Dict


def normalize_float(val: Any, decimals: int = 6) -> str:
    """Normalize float to fixed decimal places for hashing."""
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        return f"{float(val):.{decimals}f}"
    return str(val)


def hash_slot(slot: Dict, fee_bps: int) -> str:
    """Compute sha256 hash of key fields for a slot at given fee tier."""
    fee_result = None
    if "fee_results" in slot:
        for fr in slot["fee_results"]:
            if fr.get("fee_bps") == fee_bps:
                fee_result = fr
                break

    if not fee_result:
        return ""

    fields = [
        str(slot.get("window_offset", "?")),
        str(slot.get("hold_bars", "?")),
        str(slot.get("exit_type", "?")),
        normalize_float(fee_result.get("combined_oos_pf")),
        str(fee_result.get("passed", False)),
    ]

    fields_str = ",".join(fields)
    return hashlib.sha256(fields_str.encode()).hexdigest()


def main():
    v38_path = Path("docs/reports/v3.8-multi-event/report.json")
    v39_path = Path(
        "docs/reports/v3.9-cross-pair/usdjpy-regression/report.json/report.json"
    )
    out_dir = Path("docs/reports/v3.9-cross-pair/usdjpy-regression")

    # Load both reports
    with open(v38_path) as f:
        v38 = json.load(f)
    with open(v39_path) as f:
        v39 = json.load(f)

    comparison = {
        "total_slots": 288,
        "total_mismatches": 0,
        "mismatches": [],
        "hashes_v38": {},
        "hashes_v39": {},
    }

    mismatches = []

    # Compare all 288 slots (fee=2bps only as per plan)
    for event_key in ["fomc", "ecb", "nfp"]:
        v38_slots = v38.get(event_key, [])
        v39_slots = v39.get(event_key, [])

        if len(v38_slots) != len(v39_slots):
            mismatches.append(
                {
                    "type": "count_mismatch",
                    "event": event_key,
                    "v38_count": len(v38_slots),
                    "v39_count": len(v39_slots),
                }
            )
            continue

        for i, (slot38, slot39) in enumerate(zip(v38_slots, v39_slots)):
            slot_id = f"{event_key}_{i:02d}"

            # Hash at fee=2bps
            hash38 = hash_slot(slot38, 2)
            hash39 = hash_slot(slot39, 2)

            comparison["hashes_v38"][f"{slot_id}_fee2"] = hash38
            comparison["hashes_v39"][f"{slot_id}_fee2"] = hash39

            if hash38 != hash39:
                comparison["total_mismatches"] += 1

                # Extract PF values for context
                pf38 = None
                pf39 = None
                for fr in slot38.get("fee_results", []):
                    if fr.get("fee_bps") == 2:
                        pf38 = fr.get("combined_oos_pf")
                        break
                for fr in slot39.get("fee_results", []):
                    if fr.get("fee_bps") == 2:
                        pf39 = fr.get("combined_oos_pf")
                        break

                mismatches.append(
                    {
                        "slot": slot_id,
                        "v38_hash": hash38,
                        "v39_hash": hash39,
                        "v38_pf": pf38,
                        "v39_pf": pf39,
                    }
                )

    comparison["mismatches"] = mismatches

    # Write results
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "comparison.json", "w") as f:
        json.dump(comparison, f, indent=2)

    with open(out_dir / "hashes.txt", "w") as f:
        f.write("v3.8 Hashes (fee=2bps):\n")
        for slot_id in sorted(comparison["hashes_v38"].keys()):
            f.write(f"{slot_id}: {comparison['hashes_v38'][slot_id]}\n")
        f.write("\nv3.9 Hashes (fee=2bps):\n")
        for slot_id in sorted(comparison["hashes_v39"].keys()):
            f.write(f"{slot_id}: {comparison['hashes_v39'][slot_id]}\n")

    print(
        f"Comparison complete: {comparison['total_mismatches']} mismatches out of 288 slots"
    )
    if mismatches:
        print("\nFirst 5 mismatches:")
        for m in mismatches[:5]:
            print(f"  {m}")


if __name__ == "__main__":
    main()
