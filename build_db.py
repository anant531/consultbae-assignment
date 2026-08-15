import pandas as pd
import re
import os
import psycopg
from dotenv import load_dotenv
from collections import defaultdict

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

# ---------- normalization helpers ----------


def norm_phone(p):
    if pd.isna(p):
        return None
    digits = re.sub(r"\D", "", str(p))
    return digits[-10:] if len(digits) >= 10 else None


def norm_email(e):
    if pd.isna(e):
        return None
    return str(e).strip().lower()


def norm_city(c):
    if pd.isna(c):
        return None
    c = str(c).strip().lower()
    aliases = {
        "gurugram": "Gurgaon",
        "gurgaon": "Gurgaon",
        "bangalore": "Bengaluru",
        "bengaluru": "Bengaluru",
        "new delhi": "Delhi",
        "delhi ncr": "Delhi",
        "delhi": "Delhi",
        "noida": "Noida",
        "pune": "Pune",
    }
    return aliases.get(c, c.title())


def norm_ctc(val):
    """CTC column mixes raw rupees (417964) and lakhs written as decimals
    (4.2). Heuristic: anything under 100 is almost certainly lakhs written
    without the 'L' - real fresher/mid CTCs in this dataset don't go below
    ~2L or so, and none of the 'raw rupee' values are under 100000.
    This is an ASSUMPTION - document it in your data issues report."""
    if pd.isna(val):
        return None, None
    val = float(val)
    if val < 100:
        return round(val * 100000), "assumed_lakhs"
    return round(val), "as_reported"


def parse_date(val):
    if pd.isna(val):
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%m/%d/%Y"):
        try:
            return pd.to_datetime(val, format=fmt).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
    # last resort - let pandas guess, flag as low-confidence
    try:
        return pd.to_datetime(val, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return None


def parse_rate(val):
    """'1415/hr' -> (1415, 'hourly'); '15k/month' -> (15000, 'monthly')."""
    if pd.isna(val):
        return None, None
    s = str(val).strip().lower()
    m = re.match(r"([\d.]+)(k)?/(hr|month)", s)
    if not m:
        return None, None
    num, k, period = m.groups()
    num = float(num) * (1000 if k else 1)
    return round(num), ("hourly" if period == "hr" else "monthly")


# ---------- load + clean each source ----------


def load_source1():
    df = pd.read_csv("data/source1_naukri_applicants.csv")
    df["rid"] = ["s1_%d" % i for i in range(len(df))]
    df["norm_email"] = df["Email"].apply(norm_email)
    df["norm_phone"] = df["Phone"].apply(norm_phone)
    df["norm_city"] = df["City"].apply(norm_city)
    ctc = df["Current CTC"].apply(norm_ctc)
    df["ctc_inr"] = ctc.apply(lambda x: x[0])
    df["ctc_assumption"] = ctc.apply(lambda x: x[1])
    df["applied_date_iso"] = df["Applied Date"].apply(parse_date)
    return df


def load_source2():
    df = pd.read_csv("data/source2_gig_workers.csv")
    before = len(df)
    df = df.dropna(subset=["email_id"])
    df = df[df["email_id"].astype(str).str.contains("@", na=False)]
    dropped = before - len(df)
    if dropped:
        print(
            f"[source2] dropped {dropped} unusable row(s): 1 fully blank row, "
            f"1 column-shifted row (email_id held skill tags instead of an email)"
        )
    df = df.reset_index(drop=True)
    df["rid"] = ["s2_%d" % i for i in range(len(df))]
    df["norm_email"] = df["email_id"].apply(norm_email)
    df["norm_phone"] = None
    df["norm_city"] = df["location"].apply(norm_city)
    rate = df["rate"].apply(parse_rate)
    df["rate_value"] = rate.apply(lambda x: x[0])
    df["rate_period"] = rate.apply(lambda x: x[1])
    df["status_norm"] = df["status"].str.strip().str.lower()
    return df


def load_source3():
    df = pd.read_csv("data/source3_cbnexus_contacts.csv")
    before = len(df)
    df = df[df["Name"] != "Name"]  # repeated header row mid-file
    dropped = before - len(df)
    if dropped:
        print(
            f"[source3] dropped {dropped} repeated header row found mid-file "
            f"(file looks like 2 exports concatenated)"
        )
    df = df.reset_index(drop=True)
    df["rid"] = ["s3_%d" % i for i in range(len(df))]
    df["norm_phone"] = df["Phone Number"].apply(norm_phone)
    df["norm_email"] = None
    df["norm_city"] = df["City"].apply(norm_city)
    df["verified_bool"] = (
        df["Verified"].astype(str).str.strip().str.lower().isin(["y", "yes"])
    )
    return df


# ---------- union-find matching ----------


def match_people(s1, s2, s3):
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    all_ids = list(s1["rid"]) + list(s2["rid"]) + list(s3["rid"])
    for r in all_ids:
        parent[r] = r

    key_to_rids = defaultdict(list)
    for df in (s1, s2, s3):
        for _, row in df.iterrows():
            if pd.notna(row["norm_email"]):
                key_to_rids[("email", row["norm_email"])].append(row["rid"])
            if pd.notna(row["norm_phone"]):
                key_to_rids[("phone", row["norm_phone"])].append(row["rid"])

    for _, rids in key_to_rids.items():
        for other in rids[1:]:
            union(rids[0], other)

    clusters = defaultdict(list)
    for r in all_ids:
        clusters[find(r)].append(r)
    return list(clusters.values())


# ---------- build merged person records ----------


def build_people(clusters, s1, s2, s3):
    s1_by_rid = s1.set_index("rid").to_dict("index")
    s2_by_rid = s2.set_index("rid").to_dict("index")
    s3_by_rid = s3.set_index("rid").to_dict("index")

    people, sources_audit, skills_rows, needs_review = [], [], [], []

    for pid, rids in enumerate(clusters, start=1):
        rec = {
            "person_id": pid,
            "full_name": None,
            "email": None,
            "phone": None,
            "city": None,
            "experience_years": None,
            "ctc_inr": None,
            "ctc_assumption": None,
            "applied_date": None,
            "gig_status": None,
            "rate_value": None,
            "rate_period": None,
            "verified": None,
            "projects_completed": None,
            "in_naukri": False,
            "in_gig": False,
            "in_cbnexus": False,
        }
        names_seen, skills = set(), set()

        for rid in rids:
            src = rid.split("_")[0]
            if src == "s1":
                row = s1_by_rid[rid]
                rec["in_naukri"] = True
                rec["full_name"] = rec["full_name"] or row["Full Name"]
                names_seen.add(row["Full Name"].strip())
                rec["email"] = rec["email"] or row["norm_email"]
                rec["phone"] = rec["phone"] or row["norm_phone"]
                rec["city"] = rec["city"] or row["norm_city"]
                rec["experience_years"] = row["Experience (Years)"]
                rec["ctc_inr"] = row["ctc_inr"]
                rec["ctc_assumption"] = row["ctc_assumption"]
                rec["applied_date"] = row["applied_date_iso"]
                for sk in str(row["Skills"]).split(","):
                    skills.add(sk.strip().lower())
                sources_audit.append(
                    {
                        "person_id": pid,
                        "source": "naukri_applicants",
                        "source_row_id": rid,
                        "raw_name": row["Full Name"],
                    }
                )
            elif src == "s2":
                row = s2_by_rid[rid]
                rec["in_gig"] = True
                rec["full_name"] = rec["full_name"] or row["worker_name"]
                names_seen.add(row["worker_name"].strip())
                rec["email"] = rec["email"] or row["norm_email"]
                rec["city"] = rec["city"] or row["norm_city"]
                rec["gig_status"] = row["status_norm"]
                rec["rate_value"] = row["rate_value"]
                rec["rate_period"] = row["rate_period"]
                for sk in str(row["skill_tags"]).split(","):
                    skills.add(sk.strip().lower())
                sources_audit.append(
                    {
                        "person_id": pid,
                        "source": "gig_workers",
                        "source_row_id": rid,
                        "raw_name": row["worker_name"],
                    }
                )
            else:
                row = s3_by_rid[rid]
                rec["in_cbnexus"] = True
                rec["full_name"] = rec["full_name"] or row["Name"]
                names_seen.add(row["Name"].strip())
                rec["phone"] = rec["phone"] or row["norm_phone"]
                rec["city"] = rec["city"] or row["norm_city"]
                rec["verified"] = row["verified_bool"]
                rec["projects_completed"] = row["Projects Completed"]
                sources_audit.append(
                    {
                        "person_id": pid,
                        "source": "cbnexus_contacts",
                        "source_row_id": rid,
                        "raw_name": row["Name"],
                    }
                )

        for sk in skills:
            if sk and sk != "nan":
                skills_rows.append({"person_id": pid, "skill": sk})

        # name spelling differs across matched sources -> flag, don't silently pick one.
        # Compare case-insensitively first: CBNexus stores names in ALL CAPS, so
        # "SAHIL MALHOTRA" vs "Sahil Malhotra" is just formatting, not a real variant -
        # only flag when the names differ beyond casing (e.g. "R. Verma" vs "Rohit Verma").
        distinct_normalized = {n.lower() for n in names_seen}
        if len(distinct_normalized) > 1:
            needs_review.append(
                {
                    "person_id": pid,
                    "reason": "name_variant",
                    "detail": " / ".join(sorted(names_seen)),
                }
            )

        people.append(rec)

    return people, sources_audit, skills_rows, needs_review


def find_unmatched_name_collisions(s1, s2, s3):
    """Records that share a NAME but never got merged (no shared phone/email) -
    could be the same person under a changed contact, or two different people.
    Flag for a human decision instead of guessing."""
    all_names = defaultdict(list)
    for df, src, namecol in (
        (s1, "naukri_applicants", "Full Name"),
        (s2, "gig_workers", "worker_name"),
        (s3, "cbnexus_contacts", "Name"),
    ):
        for _, row in df.iterrows():
            all_names[row[namecol].strip().lower()].append((src, row["rid"]))
    flags = []
    for name, occurrences in all_names.items():
        rids = [o[1] for o in occurrences]
        if len(occurrences) > 1:
            flags.append((name, occurrences))
    return flags


# ---------- main ----------


def main():
    s1, s2, s3 = load_source1(), load_source2(), load_source3()
    clusters = match_people(s1, s2, s3)
    people, sources_audit, skills_rows, needs_review = build_people(
        clusters, s1, s2, s3
    )

    conn = psycopg.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("""
        DROP TABLE IF EXISTS audio_submissions;
        DROP TABLE IF EXISTS needs_review;
        DROP TABLE IF EXISTS person_skills;
        DROP TABLE IF EXISTS person_sources;
        DROP TABLE IF EXISTS people;
    """)

    cur.execute("""
        CREATE TABLE people (
            person_id INTEGER PRIMARY KEY,
            full_name TEXT,
            email TEXT,
            phone TEXT,
            city TEXT,
            experience_years REAL,
            ctc_inr INTEGER,
            ctc_assumption TEXT,
            applied_date TEXT,
            gig_status TEXT,
            rate_value INTEGER,
            rate_period TEXT,
            verified BOOLEAN,
            projects_completed INTEGER,
            in_naukri BOOLEAN,
            in_gig BOOLEAN,
            in_cbnexus BOOLEAN,
            skill_category TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE person_sources (
            id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            person_id INTEGER,
            source TEXT,
            source_row_id TEXT,
            raw_name TEXT,
            FOREIGN KEY(person_id) REFERENCES people(person_id)
        )
    """)

    cur.execute("""
        CREATE TABLE person_skills (
            id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            person_id INTEGER,
            skill TEXT,
            FOREIGN KEY(person_id) REFERENCES people(person_id)
        )
    """)

    cur.execute("""
        CREATE TABLE needs_review (
            id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            person_id INTEGER,
            reason TEXT,
            detail TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE audio_submissions (
            submission_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            person_id INTEGER,
            name TEXT,
            phone TEXT,
            file_path TEXT,
            duration_sec REAL,
            sample_rate_hz INTEGER,
            bitrate_kbps INTEGER,
            loudness_db REAL,
            quality_note TEXT,
            submitted_at TEXT,
            FOREIGN KEY(person_id) REFERENCES people(person_id)
        )
    """)

    for p in people:
        cur.execute(
            """
            INSERT INTO people (
                person_id, full_name, email, phone, city,
                experience_years, ctc_inr, ctc_assumption,
                applied_date, gig_status, rate_value, rate_period,
                verified, projects_completed,
                in_naukri, in_gig, in_cbnexus
            )
            VALUES (
                %(person_id)s, %(full_name)s, %(email)s, %(phone)s, %(city)s,
                %(experience_years)s, %(ctc_inr)s, %(ctc_assumption)s,
                %(applied_date)s, %(gig_status)s, %(rate_value)s, %(rate_period)s,
                %(verified)s, %(projects_completed)s,
                %(in_naukri)s, %(in_gig)s, %(in_cbnexus)s
            )
        """,
            p,
        )

    for r in sources_audit:
        cur.execute(
            """
            INSERT INTO person_sources (
                person_id, source, source_row_id, raw_name
            )
            VALUES (
                %(person_id)s, %(source)s, %(source_row_id)s, %(raw_name)s
            )
        """,
            r,
        )

    for s in skills_rows:
        cur.execute(
            """
            INSERT INTO person_skills (person_id, skill)
            VALUES (%(person_id)s, %(skill)s)
        """,
            s,
        )

    for n in needs_review:
        cur.execute(
            """
            INSERT INTO needs_review (person_id, reason, detail)
            VALUES (%(person_id)s, %(reason)s, %(detail)s)
        """,
            n,
        )

    conn.commit()

    print(
        f"\n{len(people)} distinct people written from {len(s1) + len(s2) + len(s3)} raw rows"
    )
    print(
        f"{len(needs_review)} people flagged with a name-spelling variant across sources"
    )

    cur.close()
    conn.close()
    print("\nDatabase written to Supabase PostgreSQL")


if __name__ == "__main__":
    main()
