"""Made-up postings for scoring deduplication, with the true job of each.

No real data is collected yet, so this builds a stand-in: fictional
companies whose jobs are posted on greenhouse, kariera or both, with the
title changes a job board copy tends to have. It also adds hard cases,
different jobs that look alike: another level of the same role, the same
role in another city, and two teams hiring the same role.

    python3 tests/dedup/make_sample.py postings
        Writes sample_postings.csv, which scripts/dedup-sample.sh loads, and
        sample_jobs.csv, the true job of each posting.

    python3 tests/dedup/make_sample.py label <exported sample>
        Fills in is_duplicate in a CSV from scripts/dedup-label-sample.sh,
        using sample_jobs.csv, and prints the rows for dedup_labels.csv.

The random seed is fixed, so the same code always gives the same postings.
"""

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent

rng = random.Random(20261027)

START = date(2026, 8, 1)
END = date(2026, 9, 30)

# (name on greenhouse, name on kariera). Two companies use a name on kariera
# that does not reduce to the same company_key, as happens in real data.
COMPANIES = [
    ("Nimbus Labs", "NIMBUS LABS Α.Ε."),
    ("Thalassa Tech", "Thalassa Tech S.A."),
    ("Ergon Analytics", "Ergon Analytics Μονοπρόσωπη Ι.Κ.Ε."),
    ("Ostria Software", "Ostria Software"),
    ("Meltemi Labs", "MELTEMI LABS ΙΚΕ"),
    ("Kerkis Systems", "Kerkis Systems Α.Ε."),
    ("Asteri Cloud", "Asteri Cloud Single Member P.C."),
    ("Ydor Fintech", "Ydor Fintech S.A."),
    ("Kipos Digital", "Kipos Digital"),
    ("Argo Payments", "Argo Payments Α.Ε."),
    ("Fengari Games", "Fengari Games Ltd"),
    ("Lithos Security", "Lithos Security Α.Ε."),
    ("Palmos Health", "Palmos Health ΙΚΕ"),
    ("Oikos Proptech", "Oikos Proptech"),
    ("Neraida AI", "Neraida AI S.A."),
    ("Ammos Retail Tech", "Ammos Retail Tech Α.Ε."),
    ("Kastro Insurtech", "Kastro Insurtech S.A."),
    ("Pyrgos Robotics", "Pyrgos Robotics ΕΠΕ"),
    ("Velos Telecom", "Velos Telecom Α.Ε."),
    ("Zefyros Logistics", "Zefyros Logistics Α.Ε."),
    ("Selini Media", "Selini Media Α.Ε."),
    ("Ilios Energy Tech", "Ilios Energy Tech S.A."),
    ("Taxidi Travel Tech", "Taxidi Travel Tech ΙΚΕ"),
    ("Polis Mobility", "Polis Mobility"),
    # kariera names that do not match
    ("Kyma Software", "Kyma Software Engineering"),
    ("Delta Pi Solutions", "ΔΕΛΤΑ ΠΙ ΛΥΣΕΙΣ ΠΛΗΡΟΦΟΡΙΚΗΣ Α.Ε."),
]

# Base roles: (english title, greek title, department)
ROLES = [
    ("Backend Engineer (Java)", "Μηχανικός Λογισμικού Backend (Java)", "Engineering"),
    ("Backend Engineer (Python)", "Μηχανικός Λογισμικού Backend (Python)", "Engineering"),
    ("Backend Developer (Node.js)", "Προγραμματιστής Backend (Node.js)", "Engineering"),
    (".NET Developer", "Προγραμματιστής .NET", "Engineering"),
    ("Frontend Engineer (React)", "Προγραμματιστής Frontend (React)", "Engineering"),
    ("Frontend Developer (Angular)", "Προγραμματιστής Frontend (Angular)", "Engineering"),
    ("Full Stack Developer", "Προγραμματιστής Full Stack", "Engineering"),
    ("Data Engineer", "Μηχανικός Δεδομένων", "Data"),
    ("Data Analyst", "Αναλυτής Δεδομένων", "Data"),
    ("Data Scientist", "Επιστήμονας Δεδομένων", "Data"),
    ("Machine Learning Engineer", "Μηχανικός Μηχανικής Μάθησης", "Data"),
    ("DevOps Engineer", "Μηχανικός DevOps", "Platform"),
    ("Site Reliability Engineer", "Μηχανικός Αξιοπιστίας Συστημάτων (SRE)", "Platform"),
    ("Cloud Engineer (AWS)", "Μηχανικός Cloud (AWS)", "Platform"),
    ("QA Automation Engineer", "Μηχανικός Αυτοματοποιημένων Ελέγχων (QA)", "Quality"),
    ("Android Developer", "Προγραμματιστής Android", "Mobile"),
    ("iOS Developer", "Προγραμματιστής iOS", "Mobile"),
    ("BI Developer", "Προγραμματιστής BI", "Data"),
    ("Embedded Software Engineer", "Μηχανικός Ενσωματωμένου Λογισμικού", "Engineering"),
    ("Security Engineer", "Μηχανικός Ασφάλειας Πληροφοριών", "Security"),
]

SENIORITY = ["", "", "", "Junior ", "Senior ", "Senior ", "Lead "]

# (city, greenhouse location, kariera location)
CITIES = [
    ("Athens", "Athens, Attica, Greece", "Αθήνα, Αττική, Ελλάδα"),
    ("Athens", "Athens, Greece", "Μαρούσι, Αττική, Ελλάδα"),
    ("Athens", "Athens", "Χαλάνδρι, Αττική, Ελλάδα"),
    ("Thessaloniki", "Thessaloniki, Greece", "Θεσσαλονίκη, Κεντρική Μακεδονία, Ελλάδα"),
    ("Patras", "Patras, Greece", "Πάτρα, Δυτική Ελλάδα, Ελλάδα"),
    ("Heraklion", "Heraklion, Crete, Greece", "Ηράκλειο, Κρήτη, Ελλάδα"),
]
CITY_WEIGHTS = [5, 3, 2, 3, 1, 1]

# (greenhouse workplace, kariera workplace)
WORK_MODES = [
    ("On-site", "DISABLED"),
    ("Hybrid", "PARTIALLY_REMOTE"),
    ("Remote: Yes", "FULLY_REMOTE"),
]
WORK_MODE_WEIGHTS = [3, 4, 1]

TEAMS = ["Payments", "Platform", "Growth", "Core Banking", "Mobile App", "Search", "Risk"]

next_id = {"greenhouse": 100, "kariera": 500}


def new_id(source):
    next_id[source] += 1
    return f"sample-{source[0]}{next_id[source]}"


def kariera_title(title, city_gr):
    """The way a job board copy of an ATS title tends to differ."""
    city_short = city_gr.split(",")[0]
    variants = [
        lambda t: t,
        lambda t: t,
        lambda t: f"{t} - {city_short}",
        lambda t: f"{t} (Αθήνα)" if "Αθήνα" in city_gr else f"{t} ({city_short})",
        lambda t: t.replace("Engineer", "Developer"),
        lambda t: t.replace("Senior ", "Sr. "),
        lambda t: f"{t} (m/f)",
        lambda t: f"{t} | Hybrid",
        lambda t: t.upper(),
    ]
    return rng.choice(variants)(title)


postings = []
truth = []
job_counter = 0


def add_posting(job, source, title, company, location, workplace, department, first, last):
    sid = new_id(source)
    postings.append(
        {
            "source": source,
            "source_job_id": sid,
            "first_seen": first.isoformat(),
            "last_seen": min(last, END).isoformat(),
            "title": title,
            "company": company,
            "location": location,
            "workplace": workplace,
            "department": department,
        }
    )
    truth.append({"source": source, "source_job_id": sid, "job": job})


def random_dates(earliest=START, latest=END - timedelta(days=12)):
    first = earliest + timedelta(days=rng.randint(0, (latest - earliest).days))
    last = first + timedelta(days=rng.randint(8, 40))
    return first, last


def new_job(company, role=None, seniority=None, city=None, mode=None, team=None):
    """One real-world job and its postings."""
    global job_counter
    job_counter += 1
    job = f"job{job_counter:03d}"
    gh_name, ka_name = company
    en, gr, dept = role or rng.choice(ROLES)
    sen = rng.choice(SENIORITY) if seniority is None else seniority
    city = city or rng.choices(CITIES, CITY_WEIGHTS)[0]
    mode = mode or rng.choices(WORK_MODES, WORK_MODE_WEIGHTS)[0]
    title = f"{sen}{en}" + (f" - {team}" if team else "")
    first, last = random_dates()

    # Where the job is posted
    layout = rng.choices(
        ["both", "greenhouse", "kariera", "both_greek", "kariera_repost", "both_repost"],
        [10, 4, 4, 2, 2, 1],
    )[0]

    if layout in ("both", "both_greek", "both_repost", "greenhouse"):
        add_posting(job, "greenhouse", title, gh_name, city[1], mode[0], dept, first, last)
    if layout in ("both", "both_repost", "kariera", "kariera_repost", "both_greek"):
        ka_first = first + timedelta(days=rng.randint(0, 6))
        ka_last = ka_first + timedelta(days=rng.randint(10, 35))
        if layout == "both_greek":
            ka_title = f"{sen}{gr}" + (f" - {team}" if team else "")
        else:
            ka_title = kariera_title(title, city[2])
        # Job boards sometimes leave the location out
        location = city[2] if rng.random() > 0.1 else None
        add_posting(job, "kariera", ka_title, ka_name, location, mode[1], "IT", ka_first, ka_last)
        if layout in ("kariera_repost", "both_repost"):
            # Posted again after the first copy closed, within the window
            re_first = ka_last + timedelta(days=rng.randint(3, 15))
            re_last = re_first + timedelta(days=rng.randint(10, 30))
            if re_first < END - timedelta(days=2):
                add_posting(
                    job,
                    "kariera",
                    kariera_title(title, city[2]),
                    ka_name,
                    city[2],
                    mode[1],
                    "IT",
                    re_first,
                    re_last,
                )
    return en, gr, dept, sen, city, mode


def make_postings():
    global job_counter
    for company in COMPANIES:
        # A few jobs per company that look nothing alike
        roles = rng.sample(ROLES, rng.randint(3, 6))
        for role in roles:
            _, _, _, sen, city, mode = new_job(company, role=role)
            kind = rng.random()
            # Hard cases: a second, different job that looks a lot like this one
            if kind < 0.10:
                # Another level of the same role
                other = "Senior " if sen.strip() != "Senior" else ""
                new_job(company, role=role, seniority=other, city=city, mode=mode)
            elif kind < 0.17:
                # The same role in another city
                others = [c for c in CITIES if c[0] != city[0]]
                new_job(company, role=role, seniority=sen, city=rng.choice(others), mode=mode)
            elif kind < 0.23:
                # Two teams hiring the same role
                t1, t2 = rng.sample(TEAMS, 2)
                new_job(company, role=role, seniority=sen, city=city, mode=mode, team=t1)
                new_job(company, role=role, seniority=sen, city=city, mode=mode, team=t2)

    # Postings with no company never pair, but real data has them
    for _ in range(5):
        job_counter += 1
        en, _, _ = rng.choice(ROLES)
        first, last = random_dates()
        add_posting(
            f"job{job_counter:03d}",
            "kariera",
            en,
            None,
            "Αθήνα, Αττική, Ελλάδα",
            "DISABLED",
            "IT",
            first,
            last,
        )

    with open(HERE / "sample_postings.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(postings[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(postings)
    with open(HERE / "sample_jobs.csv", "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["source", "source_job_id", "job"], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(truth)
    print(f"{len(postings)} postings of {job_counter} jobs")


def label(sample_path):
    with open(HERE / "sample_jobs.csv") as f:
        jobs = {(r["source"], r["source_job_id"]): r["job"] for r in csv.DictReader(f)}
    with open(sample_path) as f:
        pairs = list(csv.DictReader(f))
    keys = ["source_a", "source_job_id_a", "source_b", "source_job_id_b"]
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow([*keys, "is_duplicate"])
    for pair in sorted(pairs, key=lambda p: [p[k] for k in keys]):
        same = (
            jobs[pair["source_a"], pair["source_job_id_a"]]
            == jobs[pair["source_b"], pair["source_job_id_b"]]
        )
        writer.writerow([*(pair[k] for k in keys), str(same).lower()])


if __name__ == "__main__":
    if sys.argv[1:] == ["postings"]:
        make_postings()
    elif len(sys.argv) == 3 and sys.argv[1] == "label":
        label(sys.argv[2])
    else:
        sys.exit(__doc__)
