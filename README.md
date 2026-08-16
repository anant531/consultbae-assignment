# ConsultBae Take-Home Assignment

## Task 4 — Data Issues Report

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
