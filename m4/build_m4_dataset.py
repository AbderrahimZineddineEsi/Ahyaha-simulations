#!/usr/bin/env python3
"""Build the M4 training table (one row per crash occupant, one yes/no label per body region).

Inputs:
  - the M3 table (m3/data/m3_training.csv): occupants with the cleaned crash + profile features,
    so M4 sees exactly the same inputs as M3;
  - the CISS injury tables, one folder per year, as NHTSA publishes them:

        <raw>/ciss/2017/INJURY.CSV   <raw>/ciss/2018/INJURY.CSV   ...

What it does:
  1. Reads every injury (one row per injury, an occupant can have dozens).
  2. Body region = first digit of the AIS code (1 head ... 8 legs). Severity = AIS 1-6, 9 = unknown.
  3. Per occupant and region keeps the worst injury, and labels the region "injured" at AIS 2+.
  4. Joins onto the M3 occupants. An occupant with known MAIS and no injury row is uninjured
     everywhere; an occupant whose MAIS is unknown is dropped.

NASS-CDS 2010-15 is not used yet: its injury table (OI) is not downloaded. Add a nass/ folder
with oi.sas7bdat per year and extend read_injuries() to bring in ~24k more occupants.

    python build_m4_dataset.py --raw raw --m3 ../m3/data/m3_training.csv --out data/m4_training.csv
"""
import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ENCODING = "iso-8859-1"

# AIS body regions (first digit of the AIS 2005/2008/2015 code). 0 and 9 (unspecified, burns,
# whole body) are left out: the hospital twin has no place to draw them.
REGIONS = {1: "head", 2: "face", 3: "neck", 4: "thorax", 5: "abdomen", 6: "spine", 7: "arms", 8: "legs"}
# AIS 2 = "moderate": the first level that usually needs hospital care (fracture, concussion...).
# A design choice, change here if the team decides otherwise.
INJURED_AT = 2
UNKNOWN_AIS = 9


def clean_id(s):
    """Strip spaces and leading zeros so '01' and '1' join (same rule as build_m3_dataset.py)."""
    return s.astype(str).str.strip().str.replace(r"^0+(?=.)", "", regex=True)


def read_injuries(raw):
    """All CISS injuries, with the same occupant key as the M3 table: year_CASENUMBER_VEH_OCC."""
    parts = []
    for folder in sorted(p for p in (raw / "ciss").iterdir() if p.is_dir()):
        year = int(re.search(r"(?:19|20)\d\d", folder.name).group())
        path = next(p for p in folder.iterdir() if p.name.upper() == "INJURY.CSV")
        df = pd.read_csv(path, dtype=str, encoding=ENCODING)
        df.columns = [c.strip().upper() for c in df.columns]
        df["key"] = (f"{year}_" + df["CASENUMBER"].str.strip() + "_"
                     + clean_id(df["VEHNO"]) + "_" + clean_id(df["OCCNO"]))
        df["region"] = (pd.to_numeric(df["AISCODE"]) // 100000).astype(int)
        df["ais"] = pd.to_numeric(df["AIS"])
        parts.append(df[["key", "region", "ais"]].assign(year=year))
        print(f"ciss {year}: {len(df):>6,} injuries  {df['key'].nunique():>5,} injured occupants")
    return pd.concat(parts, ignore_index=True)


def region_labels(inj):
    """Per occupant: worst AIS per region, injured flag (AIS 2+), and whether the region is uncertain."""
    inj = inj[inj["region"].isin(REGIONS)]
    known = inj[inj["ais"] != UNKNOWN_AIS]
    worst = known.pivot_table(index="key", columns="region", values="ais", aggfunc="max")
    # A region with an injury of unknown severity and no known AIS 2+ there: we can't say no.
    unsure = inj[inj["ais"] == UNKNOWN_AIS].groupby(["key", "region"]).size().unstack()
    out = pd.DataFrame(index=inj["key"].unique())
    for code, name in REGIONS.items():
        w = worst[code] if code in worst else pd.Series(dtype=float)
        u = unsure[code] if code in unsure else pd.Series(dtype=float)
        out[f"ais_{name}"] = w.reindex(out.index).fillna(0)
        injured = (out[f"ais_{name}"] >= INJURED_AT).astype(float)
        out[f"inj_{name}"] = injured.where(injured.eq(1) | u.reindex(out.index).isna())
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default="raw", help="folder with the ciss/ subfolder")
    ap.add_argument("--m3", default="../m3/data/m3_training.csv", help="M3 training table (features)")
    ap.add_argument("--out", default="data/m4_training.csv")
    args = ap.parse_args()

    occ = pd.read_csv(args.m3, low_memory=False)
    occ = occ[occ["source"] == "ciss"].copy()
    print(f"M3 table: {len(occ):,} CISS occupants")

    labels = region_labels(read_injuries(Path(args.raw)))
    missing = set(labels.index) - set(occ["key"])
    print(f"Injured occupants not in the M3 table (filtered out there): {len(missing):,}")

    n_before = len(occ)
    occ = occ[occ["mais"].notna()]
    print(f"Dropped {n_before - len(occ):,} occupants with unknown MAIS (injuries not assessed)")

    df = occ.merge(labels, left_on="key", right_index=True, how="left")
    for name in REGIONS.values():  # no injury row = uninjured there
        df[f"ais_{name}"] = df[f"ais_{name}"].fillna(0)
        df[f"inj_{name}"] = df[f"inj_{name}"].where(df["key"].isin(labels.index), 0.0)
    inj_cols = [f"inj_{n}" for n in REGIONS.values()]
    df["n_regions"] = df[inj_cols].sum(axis=1)

    # Sanity check: the worst region should never exceed the person's MAIS from the OCC table.
    worst = df[[f"ais_{n}" for n in REGIONS.values()]].max(axis=1)
    bad = (worst > df["mais"]).mean()
    print(f"Rows where worst region AIS > MAIS: {bad:.2%}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    groups = json.loads(Path(args.m3).with_suffix(".columns.json").read_text())
    groups["m4_label"] = inj_cols
    groups["m4_label_info"] = [f"ais_{n}" for n in REGIONS.values()] + ["n_regions"]
    out.with_suffix(".columns.json").write_text(json.dumps(groups, indent=2))

    print(f"\nWrote {len(df):,} occupants to {out}")
    print(f"\nInjured at AIS {INJURED_AT}+ per region (share of occupants, unknown in brackets):")
    for c in inj_cols:
        print(f"  {c[4:]:<8} {df[c].mean():6.1%}  ({df[c].isna().mean():.1%})")
    print(f"\nRegions injured per person:\n{df['n_regions'].value_counts().sort_index().to_string()}")
    print(f"\nWith delta-V known (what the model will train on): {df['dv_kmh'].notna().sum():,}")


if __name__ == "__main__":
    main()
