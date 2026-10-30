#!/usr/bin/env python3
"""Build the M3 training table (one row per crash occupant) from NHTSA NASS-CDS + CISS.

Expected input: the tables NHTSA publishes (.csv for CISS, .sas7bdat for NASS-CDS 2010-2015),
all tables of one year in one folder:

    <raw>/nass/2010/oa.sas7bdat   gv.sas7bdat  ve.sas7bdat   event.sas7bdat   airbag.sas7bdat
    <raw>/ciss/2017/OCC.csv       GV.csv       CDC.csv       AIRBAG.csv       ...

What it does, per year folder:
  1. Takes the occupant table (OCC) as the base: one row per person.
  2. Builds join keys (year + case + vehicle + occupant) so every table lines up.
  3. Pulls each field from whichever table holds it (column names differ between
     NASS-CDS, CISS and CISS years, so each field has a list of candidate names).
  4. Collapses vehicle-level tables that have several rows per vehicle (CDC, AIRBAG, EVENT)
     into one value per vehicle: max delta-V, any airbag, number of events.
  5. Cleans unknown/sentinel codes to NaN, derives the label (MAIS 3+, 4 severity levels).
Then stacks all years, applies the filters and writes a CSV plus a column-group JSON that
says which columns the blackbox can provide.

    python build_m3_dataset.py --raw raw --out data/m3_training.csv
    python build_m3_dataset.py --raw raw --inspect     # only show which table/column each field maps to
"""
import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ENCODING = "iso-8859-1"
SUFFIXES = (".csv", ".sas7bdat")
KEY_COLS = ["CASENUMBER", "PSU", "CASENO", "VEHNO", "OCCNO"]

# Candidate column names per field, in priority order. The first one found in the year's
# tables is used, so a renamed column in another year only needs a name added here.
CANDIDATES = {
    "age": ["AGE"],
    "sex": ["SEX"],
    "height": ["HEIGHT"],
    "weight": ["WEIGHT"],
    "seatloc": ["SEATLOC", "SEATPOS"],
    "belt": ["BELTUSE", "MANUSE"],
    "mais": ["MAIS08", "MAIS"],  # NASS-CDS: MAIS08 = AIS 2008 (closer to CISS's AIS 2015), MAIS = old AIS 90
    "iss": ["ISS08", "ISS"],
    "death": ["DEATH"],
    "injsev": ["PARINJSEV", "INJSEV"],
    "casewgt": ["CASEWGT", "RATWGT"],
    "bodytype": ["BODYTYPE"],
    "modelyr": ["MODELYR"],
    "rollover": ["ROLLTYPE", "ROLLOVER", "ROLLTURN"],
    "dv": ["DVTOTAL"],
    "pdof": ["PDOF", "PDOF1"],
    "airbag": ["BAGDEPLOY", "BAGDEPLY"],
    "eventno": ["EVENTNO"],
}
# Occupant table: OCC in CISS, OA ("occupant assessment") in NASS-CDS.
OCC_TABLES = ("OCC", "OCCUPANT", "OA")
# Tables to look in first when several hold the same column name.
PREFER = {
    **{f: OCC_TABLES for f in ("age", "sex", "height", "weight", "seatloc", "belt",
                               "mais", "iss", "death", "injsev", "casewgt")},
    "bodytype": ("GV", "VEHICLE"), "modelyr": ("GV", "VEHICLE"), "rollover": ("GV", "VEHICLE"),
    "dv": ("CDC", "GV"), "pdof": ("CDC", "VE"), "airbag": ("AIRBAG",), "eventno": ("EVENT",),
}
# NASS-CDS event table is per crash: VEHNUM hit OBJCONT (1-30 = another vehicle's number).
NASS_EVENT_COLS = {"VEHNUM", "OBJCONT", "ACCSEQ"}
WANTED = set(KEY_COLS) | NASS_EVENT_COLS | {c for cols in CANDIDATES.values() for c in cols}

# Severity levels (our M3 output). A design choice, change here if the team decides otherwise.
# low = MAIS 0-1, medium = MAIS 2, high = MAIS 3-4, extreme = MAIS 5-6 or died.
LEVELS = ["low", "medium", "high", "extreme"]
LIGHT_BODY = ("car", "utility", "van", "pickup")

# Which columns the blackbox (phone-independent, no car sensors) can supply at inference.
COLUMN_GROUPS = {
    "id": ["source", "year", "key", "casewgt"],
    "label": ["mais", "iss", "died", "mais3plus", "severity4"],
    "blackbox_sensed": ["dv_kmh", "pdof_car", "direction_car", "rollover", "multi_impact", "n_events"],
    "needs_seat_position": ["seat_side", "seat_row", "pdof_seat", "direction_seat"],
    "from_app_profile": ["age", "male", "height_cm", "weight_kg", "bmi", "body_group"],
    "car_sensor_only": ["belt_used", "airbag_deployed"],
    "info_only": ["vehicle_age", "complete_core"],
}


# ---------- reading tables ----------

def clean_id(s):
    """Strip spaces and leading zeros so '01' and '1' join."""
    return s.str.strip().str.replace(r"^0+(?=.)", "", regex=True)


def has_case_key(cols):
    return "CASENUMBER" in cols or {"PSU", "CASENO"} <= set(cols)


def add_keys(df, year):
    """Add _case, _veh, _occ join keys (prefixed with the year: case numbers repeat across years)."""
    if "CASENUMBER" in df:
        case = df["CASENUMBER"].str.strip()
    else:
        case = clean_id(df["PSU"]) + "_" + clean_id(df["CASENO"])
    df["_case"] = f"{year}_" + case
    if "VEHNO" in df:
        df["_veh"] = df["_case"] + "_" + clean_id(df["VEHNO"])
    if "OCCNO" in df and "VEHNO" in df:
        df["_occ"] = df["_veh"] + "_" + clean_id(df["OCCNO"])
    return df


def read_header(path):
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, nrows=0, encoding=ENCODING).columns
    return next(iter(pd.read_sas(path, format="sas7bdat", encoding=ENCODING, chunksize=1))).columns


def read_wanted(path):
    """Read a table keeping only the columns in WANTED, upper-cased. CSV values stay text."""
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str, encoding=ENCODING, usecols=lambda c: c.strip().upper() in WANTED)
        df.columns = [c.strip().upper() for c in df.columns]
        return df
    df = pd.read_sas(path, format="sas7bdat", encoding=ENCODING)
    df.columns = [c.strip().upper() for c in df.columns]
    df = df[[c for c in df.columns if c in WANTED]]
    for c in KEY_COLS + ["VEHNUM", "OBJCONT"]:  # SAS stores ids as floats (12.0): make them text (12) like CSV
        if c in df and pd.api.types.is_numeric_dtype(df[c]):
            df[c] = df[c].astype("Int64").astype(str)
    return df


class YearTables:
    """All tables of one survey year; reads only the columns we need, only when asked."""

    def __init__(self, folder, year):
        self.year = year
        self.paths = {p.stem.upper(): p for p in sorted(folder.iterdir()) if p.suffix.lower() in SUFFIXES}
        self.columns = {name: {c.strip().upper() for c in read_header(path)} for name, path in self.paths.items()}
        self._cache = {}

    def find(self, field):
        """(table, column) holding `field`; candidates tried in priority order, preferred tables first."""
        prefer = [t for t in PREFER.get(field, ()) if t in self.paths]
        order = prefer + [t for t in self.paths if t not in prefer]
        for col in CANDIDATES[field]:
            for table in order:
                if col in self.columns[table] and has_case_key(self.columns[table]):
                    return table, col
        return None

    def table(self, name):
        if name not in self._cache:
            self._cache[name] = add_keys(read_wanted(self.paths[name]), self.year)
        return self._cache[name]


def discover(raw):
    """Yield (source, year, folder) for every folder under raw/nass and raw/ciss that has an occupant table."""
    for source in ("nass", "ciss"):
        root = raw / source
        if not root.is_dir():
            continue
        for folder in [root] + sorted(p for p in root.rglob("*") if p.is_dir()):
            names = {f.stem.upper() for f in folder.iterdir() if f.suffix.lower() in SUFFIXES}
            if names & set(OCC_TABLES):
                m = re.search(r"(?:19|20)\d\d", str(folder.relative_to(root)))
                if not m:
                    raise SystemExit(f"Put the year in the folder name: {folder}")
                yield source, int(m.group()), folder


# ---------- cleaning rules ----------

def flag(yes, no):
    """1.0 where `yes`, 0.0 where `no`, NaN otherwise (unknown codes stay unknown)."""
    return pd.Series(np.select([yes, no], [1.0, 0.0], default=np.nan), index=yes.index)


def clean_belt(x, col):
    # CISS BELTUSE coding from the paper's appendix: 0,1 not used; 2-18 used.
    # NASS-CDS MANUSE uses the same family of codes (0 none, 4 lap+shoulder, ...); inferred from
    # its value counts, not checked against the manual.
    return flag(x.between(2, 18), x.isin([0, 1]))


def clean_roll(x, col):
    if col == "ROLLTYPE":  # paper's appendix: 0 none; 1, 2, 9 rollover
        return flag(x.isin([1, 2, 9]), x.eq(0))
    return flag(x.between(1, 98), x.eq(0))  # ROLLOVER / ROLLTURN: 0 none, 1-97 turns, 98 end-over-end


def sector(angle, labels):
    """Quadrant of a 0-360 degree direction: 0 = front, then clockwise every 90 degrees."""
    a = angle % 360
    out = np.select([(a < 45) | (a >= 315), a < 135, a < 225], [labels[0], labels[1], labels[2]], default=labels[3])
    return pd.Series(out, index=angle.index).where(angle.notna())


# ---------- one year ----------

def build_year(yt, source, year, log):
    occ_name = next(t for t in OCC_TABLES if t in yt.paths)
    out = yt.table(occ_name).drop_duplicates("_occ")[["_case", "_veh", "_occ"]].reset_index(drop=True)
    out.insert(0, "source", source)
    out.insert(1, "year", year)

    def pull(field):
        """Numeric column for every occupant, whatever table/level (occupant, vehicle, case) it lives at."""
        hit = yt.find(field)
        if hit is None:
            log.append(f"{source} {year}: {field:<9} MISSING")
            return pd.Series(np.nan, index=out.index), None
        table, col = hit
        df = yt.table(table)
        level = "_occ" if "_occ" in df else "_veh" if "_veh" in df else "_case"
        values = pd.to_numeric(df.drop_duplicates(level).set_index(level)[col], errors="coerce")
        log.append(f"{source} {year}: {field:<9} <- {table}.{col}")
        return out[level].map(values), col

    # --- person fields (profile) ---
    age, _ = pull("age")
    out["age"] = age.where(age <= 120)
    sex, _ = pull("sex")
    out["male"] = flag(sex.eq(1), sex.between(2, 6))  # CISS codes females as 2-6
    h, _ = pull("height")
    w, _ = pull("weight")
    h, w = h.where(h.between(50, 250)), w.where(w.between(2, 300))  # cm, kg; 999 = unknown
    out["height_cm"], out["weight_kg"] = h, w
    bmi = w / (h / 100) ** 2
    out["bmi"] = bmi.where(bmi.between(8, 80))

    # --- seat: front/rear row, left/middle/right ---
    seat, _ = pull("seatloc")
    seat = seat.where(seat.between(11, 98))
    out["seat_row"], out["seat_side"] = seat // 10, seat % 10  # side: 1 left, 2 middle, 3 right

    # --- restraints (car sensors) ---
    belt, belt_col = pull("belt")
    out["belt_used"] = clean_belt(belt, belt_col)

    # --- vehicle: type, age, rollover ---
    bt, _ = pull("bodytype")
    group = np.select(
        [bt.between(1, 13), bt.between(14, 19), bt.between(20, 29), bt.between(30, 39), bt.notna()],
        ["car", "utility", "van", "pickup", "other"], default="")
    out["body_group"] = pd.Series(group, index=out.index).replace("", np.nan)
    my, _ = pull("modelyr")
    my = my.where(my.between(1950, year + 1))
    out["vehicle_age"] = year - my
    roll, roll_col = pull("rollover")
    out["rollover"] = clean_roll(roll, roll_col)

    # --- crash severity: max delta-V per vehicle and the direction of that same impact ---
    out["dv_kmh"], out["pdof_car"], n_dv_rows = np.nan, np.nan, None
    hit = yt.find("dv")
    if hit and "_veh" in yt.table(hit[0]):
        table, col = hit
        df = yt.table(table).copy()
        pdof_col = next((c for c in CANDIDATES["pdof"] if c in df), None)
        log.append(f"{source} {year}: dv        <- {table}.{col}" + (f"; pdof <- {table}.{pdof_col}" if pdof_col else ""))
        df["_dv"] = pd.to_numeric(df[col], errors="coerce")
        df["_dv"] = df["_dv"].where(df["_dv"].between(0, 250))  # 999 = unknown
        df["_pdof"] = pd.to_numeric(df[pdof_col], errors="coerce") if pdof_col else np.nan
        n_dv_rows = df.groupby("_veh").size()
        best = df.sort_values("_dv", ascending=False, na_position="last").drop_duplicates("_veh").set_index("_veh")
        out["dv_kmh"], out["pdof_car"] = out["_veh"].map(best["_dv"]), out["_veh"].map(best["_pdof"])
    else:
        log.append(f"{source} {year}: dv        MISSING")
    if out["pdof_car"].isna().all():  # NASS-CDS: delta-V in GV, direction of the main impact in VE.PDOF1
        out["pdof_car"], _ = pull("pdof")
    out["pdof_car"] = out["pdof_car"].where(out["pdof_car"].between(0, 360)) % 360  # 999 = unknown

    # --- number of impacts: distinct events of the vehicle (fallback: number of CDC rows) ---
    hit = yt.find("eventno")
    nass_event = "EVENT" in yt.paths and NASS_EVENT_COLS <= yt.columns["EVENT"]
    if hit and "_veh" in yt.table(hit[0]):
        n_events = yt.table(hit[0]).groupby("_veh")["EVENTNO"].nunique()
        log.append(f"{source} {year}: n_events  <- {hit[0]}.EVENTNO")
    elif nass_event:  # count the events where the vehicle is either the striker or the one struck
        ev = yt.table("EVENT")
        striker = ev["_case"] + "_" + clean_id(ev["VEHNUM"])
        struck = (ev["_case"] + "_" + clean_id(ev["OBJCONT"])).where(pd.to_numeric(ev["OBJCONT"], errors="coerce").between(1, 30))
        both = pd.DataFrame({"_veh": pd.concat([striker, struck]), "seq": pd.concat([ev["ACCSEQ"]] * 2)}).dropna()
        n_events = both.drop_duplicates().groupby("_veh").size()
        log.append(f"{source} {year}: n_events  <- EVENT.VEHNUM/OBJCONT")
    elif n_dv_rows is not None and n_dv_rows.max() > 1:
        n_events = n_dv_rows
        log.append(f"{source} {year}: n_events  <- number of delta-V rows (no EVENT table)")
    else:  # delta-V table has one row per vehicle, so it says nothing about the number of impacts
        n_events = None
        log.append(f"{source} {year}: n_events  MISSING")
    out["n_events"] = out["_veh"].map(n_events) if n_events is not None else np.nan
    out["multi_impact"] = flag(out["n_events"] > 1, out["n_events"] == 1)

    # --- airbag: any airbag deployed in the vehicle; vehicles with no airbag record = not deployed ---
    hit = yt.find("airbag")
    out["airbag_deployed"] = np.nan
    if hit and ("_veh" in yt.table(hit[0])):
        table, col = hit
        df = yt.table(table)
        level = "_occ" if "_occ" in df else "_veh"
        deployed = pd.to_numeric(df[col], errors="coerce").isin([1, 2, 3]).groupby(df[level]).max()
        out["airbag_deployed"] = out[level].map(deployed).fillna(False).astype(float)
        log.append(f"{source} {year}: airbag    <- {table}.{col}")
    else:
        log.append(f"{source} {year}: airbag    MISSING")

    # --- seat-relative direction: mirror right-seat occupants so 90 = far side, 270 = near side ---
    mirrored = (360 - out["pdof_car"]) % 360
    out["pdof_seat"] = out["pdof_car"].where(out["seat_side"] != 3, mirrored).where(out["seat_side"].notna())
    out["direction_car"] = sector(out["pdof_car"], ["front", "right", "rear", "left"])
    out["direction_seat"] = sector(out["pdof_seat"], ["front", "far", "rear", "near"])

    # --- labels ---
    mais, _ = pull("mais")
    mais = mais.where(mais.between(0, 6))  # 7-9 = injured/unknown severity
    iss, _ = pull("iss")
    death, _ = pull("death")
    injsev, _ = pull("injsev")
    out["mais"], out["iss"] = mais, iss.where(iss.between(0, 75))
    out["died"] = (death.between(1, 95) | injsev.eq(4)).astype(int)
    severity = np.select(
        [out["died"].eq(1) | (mais >= 5), mais >= 3, mais == 2, mais <= 1],
        ["extreme", "high", "medium", "low"], default="")
    out["severity4"] = pd.Series(severity, index=out.index).replace("", np.nan)
    out["mais3plus"] = flag(out["severity4"].isin(["high", "extreme"]), out["severity4"].isin(["low", "medium"]))

    cw, _ = pull("casewgt")
    out["casewgt"] = cw
    out["key"] = out["_occ"]
    out["complete_core"] = out[["dv_kmh", "pdof_car", "age", "male"]].notna().all(axis=1)
    return out.drop(columns=["_case", "_veh", "_occ"])


# ---------- main ----------

def inspect(folders):
    for source, year, folder in folders:
        yt = YearTables(folder, year)
        print(f"\n== {source} {year}: {len(yt.paths)} tables: {', '.join(yt.paths)}")
        for field in CANDIDATES:
            hit = yt.find(field)
            print(f"  {field:<9} -> {'.'.join(hit) if hit else 'NOT FOUND'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default="raw", help="folder with nass/ and ciss/ subfolders")
    ap.add_argument("--out", default="data/m3_training.csv")
    ap.add_argument("--inspect", action="store_true", help="only show where each field is found")
    ap.add_argument("--all-vehicles", action="store_true", help="keep buses, trucks, motorcycles")
    args = ap.parse_args()

    folders = list(discover(Path(args.raw)))
    if not folders:
        raise SystemExit(f"No OCC table found under {args.raw}/nass or {args.raw}/ciss")
    if args.inspect:
        return inspect(folders)

    log, frames, steps = [], [], []
    for source, year, folder in folders:
        df = build_year(YearTables(folder, year), source, year, log)
        n_all = len(df)
        if not args.all_vehicles and df["body_group"].notna().any():
            df = df[df["body_group"].isin(LIGHT_BODY)]
        n_light = len(df)
        df = df[df["severity4"].notna()]  # drop occupants whose injury level is unknown
        steps.append(dict(source=source, year=year, occupants=n_all, light_vehicles=n_light,
                          labeled=len(df), complete_core=int(df["complete_core"].sum())))
        frames.append(df)
    data = pd.concat(frames, ignore_index=True)

    columns = [c for group in COLUMN_GROUPS.values() for c in group]
    data = data[columns]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(out, index=False)
    out.with_suffix(".columns.json").write_text(json.dumps(COLUMN_GROUPS, indent=2))

    print("\n".join(log))
    print("\nRows kept at each step:\n" + pd.DataFrame(steps).to_string(index=False))
    print(f"\nTotal: {len(data)} occupants, {int(data['complete_core'].sum())} with delta-V, direction, age and sex")
    print("\nSeverity levels:\n" + data["severity4"].value_counts().reindex(LEVELS).to_string())
    print("\nShare missing per column:\n" + data.isna().mean().round(3).to_string())
    print(f"\nWrote {out} and {out.with_suffix('.columns.json')}")


if __name__ == "__main__":
    main()
