# ConsultBae Take-Home Assignment

## Project Overview

This project implements an end-to-end candidate data and automation pipeline for ConsultBae.

The solution:

1. Cleans and merges three messy candidate datasets.
2. Resolves people across the source systems using normalized identifying fields.
3. Stores the consolidated data in Supabase-hosted PostgreSQL.
4. Uses n8n and an LLM to automatically classify candidate skills.
5. Provides a Streamlit audio submission application.
6. Stores uploaded audio and extracted audio metadata in Supabase.
7. Uses an event-driven Supabase → n8n webhook so new candidates can be classified automatically.

---

## Architecture

```text
                    ┌──────────────────────┐
                    │  Source CSV Files    │
                    │                      │
                    │  Naukri              │
                    │  Gig Workers         │
                    │  CBNexus             │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │     build_db.py      │
                    │                      │
                    │ Cleaning             │
                    │ Normalization        │
                    │ Entity Resolution    │
                    └──────────┬───────────┘
                               │
                               ▼
                 ┌────────────────────────────┐
                 │ Supabase PostgreSQL        │
                 │                            │
                 │ people                     │
                 │ person_sources             │
                 │ person_skills              │
                 │ needs_review               │
                 │ audio_submissions          │
                 └────────────┬───────────────┘
                              │
             ┌────────────────┴─────────────────┐
             │                                  │
             ▼                                  ▼
   ┌────────────────────┐             ┌────────────────────┐
   │       n8n           │             │     Streamlit      │
   │                    │             │                    │
   │ Supabase Webhook   │             │ Candidate details  │
   │       ↓            │             │ Audio upload       │
   │ Fetch candidate    │             │ Metadata extraction│
   │       ↓            │             │       ↓            │
   │ OpenAI LLM         │             │ Supabase Storage   │
   │       ↓            │             │       +            │
   │ Update category    │             │ audio_submissions  │
   └────────────────────┘             └────────────────────┘
```

---

# Task 1 — Data Merge and Database

The three source CSV files are:

```text
data/source1_naukri_applicants.csv
data/source2_gig_workers.csv
data/source3_cbnexus_contacts.csv
```

The pipeline is implemented in `build_db.py`.

### Data processing flow

```text
CSV files
    ↓
Load source data
    ↓
Clean malformed rows
    ↓
Normalize phone/email/city/CTC/date/rate fields
    ↓
Resolve people across sources
    ↓
Create audit records
    ↓
Write to PostgreSQL
```

### Entity resolution

There is no single person ID shared across all three source systems.

The matching relationships are:

```text
Source 1 ↔ Source 2 → email
Source 1 ↔ Source 3 → phone
```

Normalized email and phone values are therefore used to create connections between records.

The records are treated as a graph and connected components are used to determine which source records belong to the same person.

Names are deliberately not used as a primary merge key because two different people can have the same name.

Records where only the name matches are not silently merged and can instead be flagged for review.

### Auditability

The original source relationships are retained in `person_sources`, so merged records can be traced back to their source records.

The pipeline also stores unresolved review cases in `needs_review`.

### Database tables

The PostgreSQL database contains:

- `people` — consolidated person-level records
- `person_sources` — source-level audit trail
- `person_skills` — normalized skills associated with people
- `needs_review` — records requiring manual review
- `audio_submissions` — submitted audio and extracted metadata

### Running the data pipeline

```bash
python build_db.py
```

The script requires a `DATABASE_URL` environment variable pointing to the PostgreSQL database.

---

# Task 2 — AI Automation with n8n

The AI automation classifies candidates into skill categories based on their associated skills.

The categories used by the workflow are:

- `automation-heavy`
- `web dev`
- `data`

### Automation flow

```text
New candidate inserted into Supabase
             ↓
Supabase Database Webhook
             ↓
n8n Production Webhook
             ↓
Fetch candidate + skills
             ↓
OpenAI LLM
             ↓
Skill classification
             ↓
Update people.skill_category
```

The workflow is event-driven rather than periodically checking the entire database.

Only a new candidate event triggers the workflow, reducing unnecessary LLM executions.

### Production webhook

The Supabase `people` table has an INSERT webhook configured to call the n8n production webhook.

The production endpoint is used rather than the n8n test endpoint.

### Validation

The production webhook was tested using a controlled candidate record.

The candidate's skills were:

```text
Python
n8n
SQL
```

The workflow successfully classified the candidate as:

```text
automation-heavy
```

The test candidate was subsequently removed and the production database was verified to contain:

```text
60 candidates
0 unclassified
```

---

# Task 3 — Mini Audio Collection App

The audio collection application is implemented using Streamlit in `app.py`.

The application allows a candidate to:

1. Enter their full name.
2. Enter their phone number.
3. Upload an audio file.
4. Preview the audio.
5. Submit the audio.

Supported upload formats include:

```text
WAV
MP3
M4A
OGG
WEBM
```

### Audio processing

For every submitted audio file, the application extracts:

- Duration
- Sample rate
- Bitrate
- Loudness

Loudness is represented using dBFS.

Sample rate is stored in Hz in the database and displayed in kHz in the application.

### Audio storage

Uploaded audio files are stored in the Supabase Storage bucket:

```text
audio-submissions
```

A corresponding record is inserted into:

```text
audio_submissions
```

The database record stores the candidate relationship, file path, extracted metadata, quality note, and submission timestamp.

### Candidate matching

The application normalizes the submitted phone number and attempts to match it against the `people` table.

If a matching person is found, the audio submission is associated with that `person_id`.

If no person is found, the submission can still be stored without a `person_id`.

### Running the application

```bash
streamlit run app.py
```

---

# Task 4 — Data Issues Report

The three source datasets contain inconsistent formatting, mixed representations, malformed records, and entity-resolution challenges. The cleaning pipeline normalizes these differences before merging records into the central PostgreSQL database.

### 1. Phone number formatting

**Problem:** Phone numbers appear in different formats, including country codes, leading zeros, spaces, and hyphens.

**Handling:** Phone values are converted to digits only and the last 10 digits are retained when at least 10 digits are present. This provides a consistent representation for matching records across sources.

### 2. Email formatting

**Problem:** Email addresses have inconsistent casing and may contain surrounding whitespace.

**Handling:** Email values are stripped of surrounding whitespace and converted to lowercase before matching.

### 3. City name inconsistencies

**Problem:** City names differ in casing, whitespace, and naming conventions. Examples include `Gurgaon`/`Gurugram` and `Bangalore`/`Bengaluru`.

**Handling:** City values are trimmed, normalized for casing, and mapped to canonical city names using an alias dictionary.

### 4. Mixed CTC units

**Problem:** The CTC field contains both raw INR values, such as `417964`, and decimal values representing lakhs, such as `4.2`.

**Handling:** A documented heuristic is used: values below 100 are interpreted as lakhs and multiplied by 100,000; larger values are treated as INR as reported. The normalized INR value and the assumption used for conversion are retained separately.

**Caveat:** This is an assumption based on the observed dataset and should be treated as a data-quality limitation rather than a universally valid rule.

### 5. Multiple date formats

**Problem:** Dates occur in several formats, including formats such as `DD-MM-YYYY`, `YYYY-MM-DD`, `DD Mon YYYY`, and slash-separated dates. Some slash-separated dates can be genuinely ambiguous.

**Handling:** The pipeline attempts known formats first and then uses a `dayfirst=True` fallback for values that do not match the explicit formats.

### 6. Unusable rows in source2

**Problem:** Source2 contains two unusable records:

- one fully blank row
- one column-shifted row where the `email_id` field contains skill-tag data instead of an email address

**Handling:** Both rows are removed because they cannot reliably represent a worker record. The pipeline logs the number of dropped rows during execution.

### 7. Repeated header in source3

**Problem:** Source3 contains a repeated header row in the middle of the file, indicating that two exports were concatenated.

**Handling:** The repeated header row is identified and removed before processing.

### 8. Rate format inconsistency

**Problem:** The gig-worker rate field contains different billing periods, such as hourly and monthly rates, and uses representations such as `1415/hr` and `15k/month`.

**Handling:** The rate is parsed into two normalized fields:

- `rate_value`
- `rate_period`

This preserves both the numerical amount and its billing period instead of treating the values as directly comparable.

### 9. Status formatting

**Problem:** Worker status values contain inconsistent casing and whitespace.

**Handling:** Status values are stripped of surrounding whitespace and converted to lowercase.

### 10. Verified field formatting

**Problem:** The CBNexus `Verified` field uses inconsistent representations such as `Y` and `Yes`.

**Handling:** Values are normalized into a Boolean field. `Y` and `Yes` are treated as verified; other values are treated as not verified.

### 11. Name variant across matched records

**Problem:** One merged person has different name representations across sources:

`R. Verma / Rohit Verma`

The records share identifying information and were therefore connected during entity resolution, but the differing names are important enough to surface for human review.

**Handling:** The person remains one merged record, while the case is recorded in the `needs_review` table with reason `name_variant`.

The current database contains **1** such review record.

### 12. Entity-resolution ambiguity

**Problem:** The same name appearing in multiple sources does not necessarily mean the records belong to the same person. Automatically merging records based only on a name can incorrectly combine different people.

**Handling:** Name alone is not used as a merge key. The matching process uses normalized email and phone values to create connections between source records. Connected components are then treated as one person.

Records that share a name but do not have a matching identifying key are not silently merged.

### Final data reconciliation

After cleaning the source files:

- **3 malformed/redundant rows** were removed:
  - 2 unusable rows from Source 2
  - 1 repeated header row from Source 3
- **102 usable source records** remained after cleaning.
- These records resolved into **60 distinct people**.
- All **102 usable source records** are retained in `person_sources` as an audit trail.
- **257 normalized skill records** are stored in `person_skills`.
- **1 name-variant case** is stored in `needs_review`.

The resulting database is stored in Supabase PostgreSQL.

---

# Stuck Log

## 1. Entity resolution across the three source datasets

One of the hardest problems was determining when records from different source files represented the same person.

There was no single ID shared across all three datasets. Instead, different pairs of datasets shared different identifying fields. Email could connect some records while phone number could connect others.

I considered using names as a matching key, but this could incorrectly merge different people with the same name. I therefore used normalized email and phone values as matching evidence and treated the records as a graph. Connected records were resolved into a single person using connected components.

I rejected name-only matching because a matching name does not prove that two records represent the same person.

I also retained the original source relationships in `person_sources` so that merged records remain auditable.

---

## 2. Audio bitrate extraction

The audio application initially extracted duration, sample rate, and loudness successfully, but bitrate was not being populated reliably.

I investigated the audio-processing environment and checked whether `ffprobe` was available. The executable was present, but calling it through the Python environment produced GLIBC/library compatibility errors.

Rather than making the entire audio application dependent on a single failing command, I investigated alternative metadata extraction and fallback approaches.

The final implementation uses audio metadata extraction with a fallback based on the audio properties when direct bitrate metadata is unavailable.

I validated the application using a test WAV file and confirmed that the final audio submission flow could extract and display the required metadata, including a bitrate value.

This debugging process also exposed a database schema issue because the extracted bitrate could be decimal rather than an integer, which had to be handled correctly.

---

## 3. n8n test webhook versus production webhook

The most difficult integration issue was connecting the Supabase database webhook to the correct n8n endpoint.

During development, the n8n workflow worked when tested manually using the n8n test webhook. However, the Supabase INSERT event was not triggering the workflow in the same way.

I traced the issue to the distinction between the n8n test webhook and the production webhook.

The workflow was published and the Supabase Database Webhook was changed to use the production n8n webhook endpoint.

I then created a controlled test candidate and verified the complete flow:

```text
Supabase INSERT
      ↓
Supabase Database Webhook
      ↓
n8n Production Webhook
      ↓
Fetch candidate + skills
      ↓
LLM classification
      ↓
Update skill_category
```

The production test successfully classified the candidate as `automation-heavy`.

The test records were then removed and the final database was verified with **60 candidates and 0 unclassified candidates**.

---

# Project Structure

```text
.
├── data/
│   ├── source1_naukri_applicants.csv
│   ├── source2_gig_workers.csv
│   └── source3_cbnexus_contacts.csv
│
├── artifacts/
│   └── ...
│
├── lib/
│   └── ...
│
├── app.py
├── build_db.py
├── requirements.txt
├── README.md
├── .replit
└── replit.md
```

---

# Tech Stack

### Data processing

- Python
- Pandas
- PostgreSQL
- psycopg

### Database

- Supabase PostgreSQL
- Supabase Storage

### AI automation

- n8n
- OpenAI LLM
- Supabase Database Webhooks

### Audio application

- Streamlit
- Pydub
- Mutagen

### Development

- Replit
- Git
- GitHub
