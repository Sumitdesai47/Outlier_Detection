"""Methodology copy for auto without-causal workflows (served as formatted Word .docx).

Each guide includes: a **quick example** near the top, a **technical pipeline**,
and a **longer worked example** at the end (invented numbers for teaching).

Body text is plain with `===` / `---` section underlines; `services/methodology_docx.py`
converts it to **Word .docx** when `python-docx` is installed, otherwise to a
**Word-compatible HTML .doc** (still opens in Microsoft Word with headings and lists).
"""

DOC_AUTO_NO_CAUSAL = """\
Auto (No Causal) — What this workflow does (simple guide)
===========================================================

In one sentence
----------------
You upload a spreadsheet of sensor readings over time. The tool finds a **quiet,
normal-looking stretch** of time, learns what “normal” looks like for each tag
from that stretch, then flags times when readings drift too far from that normal.

Example for understanding (quick)
---------------------------------
**Picture:** Monday–Friday everything looks boring and steady. Saturday one sensor
jumps much higher. The tool treats Monday–Friday as “how we expect things to look”
and highlights Saturday’s jump on that sensor in the results table and chart.

What you need
-------------
- An Excel file (.xlsx) with time in one column and **numbers** for each tag
  (either one column per tag, or a long list with Tag + Value).

What happens, step by step
--------------------------
1. The file is read and organised so each row is one moment in time and each
   column is one measurement (tag).
2. For every moment, the tool scores how **wild** all tags look together
   (big swings vs small, steady readings).
3. It looks for a **block of time** where that score is lowest — meaning the
   plant or process looked most stable then. That block is used as the
   **reference period** (you do not pick it by hand).
4. For each tag, it learns the **average** and **typical spread** of values
   **only during that reference period**.
5. It draws **bands** around “normal”: mild drift, stronger drift, and very
   strong outliers. Values outside those bands get labels like Normal, Drift,
   or Strong anomaly.
6. You get tables and charts that show **which tag**, **when**, and **how
   serious** the departure was.

How to read the results (non-technical)
----------------------------------------
- **Normal**: the value sits where the tool expects for that tag, given the
  reference period.
- **Drift / drift + anomaly / strong anomaly**: the value has moved unusually
  far from the learned normal, or stayed unusual for enough readings (so one
  random blip is treated differently from a lasting shift).

What this workflow does **not** need
-------------------------------------
- No cause-and-effect matrix.
- No separate “clean training file” — the tool guesses a calm period from
  your upload.

Technical pipeline (how the logic runs)
---------------------------------------
1) **Read data** — workbook → detect timestamp and tags → long format if needed →
   **wide pivot** (one row per time, one column per tag).

2) **Find a calm reference period** (“clean” reference in time, not manual cleaning)
   i) For each time, compute per-tag **scaled deviation** from typical level.  
   ii) Combine into a **stability score** per row (level + short-term change).  
   iii) **Smooth** that score over a sliding time window.  
   iv) Choose the window where the smoothed score is **lowest**, then trim to
       the calmest timestamps inside it.

3) **Build limits** — per tag: mean and standard deviation **only** on reference
   timestamps → add fixed-width **z-bands** (drift / drift+anomaly / strong).

4) **Classify every row** — compare each tag–time to bands; apply **persistence**
   (how long you stay outside) → **Final_Class**.

5) **Display helpers (app layer)** — **tag–tag correlation** on the wide matrix
   is used only to suggest **related tags** in the UI; it does **not** choose the
   reference period or limits.

Worked example (longer walkthrough — invented numbers)
------------------------------------------------------
Imagine two tags, **Temperature** and **Pressure**. Monday–Friday both sit in a
boring range (temp around 100 °C, pressure steady). Saturday temp suddenly reads
**130 °C** while pressure still looks fine.

The tool notices Monday–Friday was the **calmest** stretch overall, learns from
those rows that temp “usually” is about 100 with a modest spread, then marks
Saturday’s **130** as **far outside** the normal band for Temperature — that is
the kind of row you will see highlighted as abnormal in the results.
"""

DOC_AUTO_NO_CLEAN_DATA = """\
Auto (No Clean Data) — What this workflow does (simple guide)
=============================================================

In one sentence
----------------
The tool picks **one short period** where your process looked most stable, builds
**tight normal limits from that period only**, then checks the **whole timeline**
against those limits.

Example for understanding (quick)
---------------------------------
**Picture:** The tool locks onto one short “everything looked normal” hour block.
It draws tight min/max lines for each sensor **only from that block**. A reading
next week that would look fine on a whole-year chart can still be **red** here if
it sits outside those tight lines.

What you need
-------------
- Same as other auto tabs: a time-series Excel file (.xlsx).

What happens, step by step
--------------------------
1. Data is organised as time rows × tag columns.
2. The tool scores every moment for “how noisy or jumpy” the **set of tags** is.
3. It finds a **fixed-length window** of consecutive rows where that score is
   **lowest** — your automatic “good behaviour” snapshot.
4. For each tag, **only values inside that window** are used to set upper and
   lower limits (normal range). A little trimming is applied so one odd spike
   inside the window does not distort the limits.
5. Every reading in the file is compared to those limits. **Persistence** means
   a single flicker may be treated differently from a value that **stays** outside
   the band.
6. The results screen can also compare this view to a **“whole history”**
   baseline so you can see how much stricter the auto window is than using
   everything at once.

How to read the results
------------------------
- **Inside limits + stable**: normal operation relative to the chosen window.
- **Outside limits or persistent unusual values**: drift or anomaly labels
  depending on how far and how long.

Difference from “Auto Identification”
--------------------------------------
- **No Clean Data** never reuses later “similar” days — limits come **only**
  from that one detected window.

Technical pipeline (how the logic runs)
---------------------------------------
1) **Read data** → wide matrix (same ingestion as other auto tabs).

2) **Clean anchor window** (automatic “good” slice)
   i) Per time: **fraction of tags** that look “off-level” and “off-jump” using
      robust scaled scores.  
   ii) **clean_score** = weighted mix of those fractions (+ penalty if too many
       tags misbehave at once).  
   iii) **Rolling average** of clean_score over a fixed row count.  
   iv) **Pick** the contiguous slice where that average is **minimum** → anchor.

3) **Limits from anchor only** — per tag: take values in the anchor; optional
   inner **trim**; robust centre and spread; merge with tail quantiles and a
   width **margin** → Lower_Limit / Upper_Limit.

4) **Classify full history** — distance-from-limit style scoring, **persistence**
   counts, severity cutoffs → Final_Class / Final_Status.

5) **Optional comparison** — recompute limits using **all timestamps** as
   reference (std-style) and compare labels (UI metric).

6) **Display helpers** — **correlation** across tags in the wide matrix → top
   related tags in the UI only (not used in steps 2–4).

Worked example (longer walkthrough — invented numbers)
------------------------------------------------------
Suppose the tool decides **hours 08:00–10:00** were the calmest slice. From those
rows only it learns that **TankLevel** should stay roughly between **4.8 m and
5.2 m**.

At **14:00** the level reads **5.9 m** — still “possible” if you used the whole
month as normal, but **above** the tight limit learned from the calm slice — so
this tab can flag 14:00 as unusual **because** it trusts only that short good
period, not the whole history.
"""

DOC_AUTO_IDENTIFICATION = """\
Auto Identification — What this workflow does (simple guide)
==============================================================

In one sentence
----------------
Like “No Clean Data”, the tool first finds a **calm anchor window**. Then, for
each tag, it also pulls in **other days in the file that look like that anchor**
when setting limits — so “normal” can include more of your real steady running.

Example for understanding (quick)
---------------------------------
**Picture:** Monday anchor says “good flow is ~50”. Wednesday the flow is again
~50 for hours — not on Monday’s clock, but the **same kind of reading**. This tab
can treat Wednesday-like rows as extra evidence of “normal”, so limits are less
tight than if Monday alone were the whole story.

What you need
-------------
- A time-series Excel file (.xlsx), same format as the other auto tabs.

What happens, step by step
--------------------------
1. Build the time × tags table from your file.
2. Find the **anchor window** (same idea as No Clean Data: a stretch that looks
   most stable).
3. For each tag, add historical points that are **close to the anchor** in value
   (not only points inside the anchor dates). That gives a richer picture of
   “normal” without you labelling data by hand.
4. The tool cleans that pool a bit so obvious outliers do not widen the limits
   too much, then sets **upper and lower limits** from the result.
5. The full timeline is scored against those limits, with the same style of
   severity labels as No Clean Data.
6. Optional comparison on screen: limits from “whole file” vs limits from this
   smarter reference.

How to read the results
------------------------
- Same headline idea: **inside band** vs **outside / persistent unusual**.

Why use this instead of “No Clean Data”?
----------------------------------------
- If your process **returns to a good level** on many different dates, those
  days can **help define normal**, so you get fewer false alarms than if normal
  were taken from a single short window only.

Technical pipeline (how the logic runs)
---------------------------------------
1) **Read data** → wide matrix.

2) **Anchor window** — same **detect_clean_window** as No Clean Data (row
   level/jump scores → rolling mean → minimum-mean slice).

3) **Expand reference per tag**
   i) **Similar to anchor**: scaled distance from anchor median within a z cap.  
   ii) **Quantile band**: values inside an expanded band around anchor percentiles.  
   iii) Union of (i) and (ii); if too few points, **relax** z or fall back to
       anchor-only.

4) **Trim expanded pool** — second robust pass: drop extreme candidates if enough
   rows remain.

5) **Limits** — robust median ± k·scale, widen with low/high quantiles, add margin.

6) **Classify** — `generate_without_causal_results` (same family as No Clean Data).

7) **Display helpers** — **correlation** matrix → related tags in UI; optional
   per-tag prediction helpers in the app — not used for steps 2–5.

Worked example (longer walkthrough — invented numbers)
------------------------------------------------------
**Anchor:** Monday morning **Flow** was steady near **50** for one hour — chosen
as the calm window.

**Later:** on Wednesday **Flow** is again **48–52** for many hours (same feel as
Monday), but those Wednesday rows are **outside** the anchor clock-times.

**No Clean Data** would ignore Wednesday when building limits. **Identification**
adds Wednesday-like values into the “normal pool” because they **look like** the
anchor in value space — so limits may end a bit **wider**, and a small Wednesday
wobble is less likely to be called drift than under No Clean Data alone.
"""

DOC_TESTING_DEVIATION_SPIKE_V4 = """\
Testing (V4) — Deviation / spike / change — Simple guide
========================================================

In one sentence
----------------
The tool builds a **rich picture of smooth vs jumpy behaviour** for each tag,
finds the **calmest continuous period**, learns limits from “days that look like
that calm period”, then flags problems using both **level** and **sudden change**
signals.

Example for understanding (quick)
---------------------------------
**Picture:** A value can sit in an “OK” band but still **lurch** in two steps
(50 → 52 → 54). V4 watches that **shape** (spike / change), not only whether you
crossed a big outer fence — so small stair-steps can still draw attention.

What you need
-------------
- A time-series Excel file (.xlsx).

What happens, in plain language
--------------------------------
1. Organise the spreadsheet as time × tags.
2. For each tag and each moment, the tool looks at:
   - Is the value far from its usual level?
   - Is there a **sudden step** compared to nearby points?
   - Is the pattern **changing quickly** or **choppy**?
3. It combines those ideas into one **“how messy is this moment?”** score across
   all tags.
4. It picks a **window of consecutive rows** where that mess score is **lowest**
   — that is the “best guess” calm reference.
5. It then collects **other times** that still look calm (similar level, no big
   jumps) to set **normal upper/lower bounds** per tag.
6. Finally it labels each point using those bounds plus rules for **how long**
   something has to stay bad before it is called drift rather than a one-off.

How to read the results
------------------------
- **Normal**: behaviour matches the calm reference and the limits built from it.
- **Drift / drift + anomaly / strong**: level or sudden moves broke the rules,
  with stronger wording when the break is large or sustained.

Comparison line on screen
---------------------------
- You may also see how this compares to “use the whole timeline as normal” —
  useful to see if the calm-period view is much stricter.

Technical pipeline (how the logic runs)
---------------------------------------
1) **Read data** → wide matrix.

2) **Per-tag deviation features**
   i) **Level** — robust z of value vs global robust centre.  
   ii) **Spike** — residual vs a short **rolling median** of the raw series, scaled.  
   iii) **Change** — change in level-z, scaled.  
   iv) **Volatility** — rolling variability of level-z, scaled.

3) **Row clean_score** — for each time: weighted **fraction of tags** that exceed
   level / spike / change / volatility thresholds (+ missing weight).

4) **Clean window** — rolling **mean** of clean_score over W rows; choose the slice
   where that mean is **minimum**.

5) **Clean-like reference** — per tag: values near anchor in value-z, with spike
   and change below caps, and row score below a **quantile** cap; anchor rows
   always kept; fallback to anchor-only if too few points.

6) **Limits** — robust median ± k·scale on reference; tails + margin.

7) **Classify** — `generate_without_causal_results` + **classify_final** (limits,
   persistence window, spike/change gates).

8) **Display helpers** — **correlation** on wide matrix for related tags in UI.

Worked example (longer walkthrough — invented numbers)
------------------------------------------------------
**Flow** sits at **50** for many hours (calm). After limits are built from a
clean-like pool, **Flow** suddenly steps to **52** then **54** in two readings —
still a small absolute change.

Because V4 tracks **sudden change** and **spike** behaviour, that quick staircase
can still raise a **change / spike** flag even if the raw number has not crossed
a huge outer limit yet — that is the “extra sensitivity to shape, not only level”
idea.
"""

DOC_TESTING_DEVIATION_SPIKE_V5 = """\
Outlier detection — Simple guide (no moving-average clean pick)
=================================================================

In one sentence
----------------
The tool finds **stable stretches** without using rolling averages, builds
**normal bands** from values similar to that stable time, then also catches
some problems that sit **inside the wide band** but show a **sudden jump**,
**sudden shift in error**, or **linger outside a tighter “typical” band**.

Example for understanding (quick)
---------------------------------
**Picture:** Outer limits say “80–120 is still legally OK”, but the **middle**
comfort zone is 95–105. You can be **inside 80–120** yet (a) **jump** suddenly, or
(b) **sit at 112** for several rows — V5 can flag those **without** leaving the
outer 80–120 box.

What you need
-------------
- A time-series Excel file (.xlsx).

What happens, in plain language
--------------------------------
1. Data is organised as time × tags.
2. Each moment gets a **stability score** from all tags together (level, step
   size, and change in deviation — without a moving-average smoother for picking
   the clean era).
3. The tool prefers the **longest run of stable moments**. If the process never
   stays calm long enough, it falls back to the **calmest individual rows** it can find.
4. For each tag it learns:
   - A **wide** normal band (typical operation range), and
   - A **narrower “middle” band** (where values usually sit when things are fine).
5. A point can be flagged because:
   - It is **outside the wide band**, or
   - It is **inside the wide band** but shows a **sharp step** or **sharp change
     in error**, or
   - It sits **between wide and middle** for **several readings in a row**
     (lingering away from the middle without crossing the outer fence).

How to read the results
------------------------
- **Normal**: none of the above patterns fired.
- **Drift / drift + anomaly / strong anomaly**: depends on how far outside the
  bands you are, how sharp the step was, and whether the issue **lasts** more
   than one reading.

Technical pipeline (how the logic runs)
---------------------------------------
1) **Read data** — workbook → timestamp + numeric tags → **wide matrix**
   (one row per time, one column per tag).

2) **Find a clean / stable reference period** (no moving average in this step)
   i) **Level** — robust z of each tag’s value at that row (cross-tag grid).  
   ii) **Deviation / motion** — robust z of first difference (**delta**) and of
       **error-change** (change in deviation from a row-wise reference).  
   iii) **Row clean_score** — mix of “how many tags look bad” on level, delta, and
        error-change.  
   iv) **Stable rows** — score below a **quantile** cutoff and small delta /
       error-change bad fractions.  
   v) **Run lengths** — count consecutive stable / unstable flags.  
   vi) **Choose period** — prefer the **longest contiguous stable run**; if too
       short, fall back to **lowest-score** individual rows (still no MA for this
       choice).

3) **Build clean-like limits per tag** (`build_clean_like_limits`)
   i) Anchor median/scale from the chosen **clean_df**.  
   ii) For all times: value-z, delta-z, error-change-z vs clean-based scales.  
   iii) **clean_like** mask: all three within candidate z caps.  
   iv) **Reference pool** = values where clean_like (or clean-only fallback).  
   v) **Broad limits** — robust band + extreme quantiles + margin → Lower / Upper.  
   vi) **Central band** — e.g. 5th–95th percentile of reference for “middle”.

4) **Score every row** (`generate_without_causal_all_results`)
   i) **Value_Z** — distance of value from reference median in reference scale.  
   ii) **Delta_Z**, **Error_Change_Z** — step and shift-in-error vs clean-learned
       centres and scales.  
   iii) **Deviation_Level_Z** — how far |error| sits vs a clean-learned abs-error
        reference.

5) **Flags (in-band vs out-of-band)**
   i) **Outside** broad Lower/Upper.  
   ii) **Within-limit spike** — inside broad band but |Delta_Z| high.  
   iii) **Within-limit error-change** — inside broad band but |Error_Change_Z| high.  
   iv) **Within-limit deviation** — inside broad band, **outside central band**,
       and deviation-level z high.

6) **Persistence** — consecutive counts on “outside limit” and on “within-limit
   deviation”; compare to point thresholds → persistent flags.

7) **Final label** — `final_class_logic`: outside-limit branches (strong vs
   persistent drift vs one-step anomaly); else within-limit spike/change →
   drift+anomaly; else persistent in-band deviation → drift; else Normal.

8) **Display helpers (app only)** — **correlation** between tags is used in the
   dashboard for “related tags”; it is **not** part of steps 2–7 above.

Worked example (longer walkthrough — invented numbers)
------------------------------------------------------
After limits exist, suppose for **Tag_X** the **wide** OK range is **80–120** and
the **middle** “typical” band is **95–105**.

- At **10:00** the value is **102** → inside both bands → **Normal**.  
- At **10:01** it jumps to **118** (still inside 80–120) but the **step** is huge
  versus the clean-era behaviour → can be flagged as a **within-limit spike**
  (sudden move without leaving the outer fence).  
- At **11:00** the value is **112** for several rows in a row — still between 80
  and 120, but **outside 95–105** long enough → can become **persistent in-band
  deviation** (drift-style signal without crossing the outer wall).

Numbers are invented; your real limits and flags come from your file and the
configured thresholds.
"""

DOC_TESTING_TOP5_CORR_REGRESSION_V6 = """
Outlier detection (using data model) — top-5 correlated regression (no causal matrix)
======================================================================================

What this tab does
------------------
For each numeric **target tag**, the engine picks the **five other tags** most
correlated with it (on stable training rows), fits a **fast ridge regression**
to predict the target from those five, then scores each timestamp using
**actual vs predicted residual**, **value range vs historical quantiles**, and
**peer-tag shift** support. There is **no causal matrix** upload.

Pipeline (matches without_causal_top5_corr_regression_fast.py)
----------------------------------------------------------------
1) **Load workbook** — first sheet, or **All_Results** if needed (long Timestamp /
   Tag / Actual_Value or wide timestamp + tag columns).

2) **Stable training mask** — rows where most tags have modest robust-z vs
   their series (stable candidate grid).

3) **Correlation** — Pearson correlation matrix on stable rows; per target,
   take **top N absolute correlations** (default five) as predictors.

4) **Ridge model** — train on stable rows; predict all rows; residual =
   actual − predicted.

5) **Residual z** — robust center/scale of residuals on training rows; **value z**
   from training value distribution; **soft / outer range flags** from 5–95%
   and 1–99% training quantiles.

6) **Peer shift** — fraction of top predictors whose robust-z exceeds a limit
   at each row (context for coordinated moves).

7) **Final class** — combines residual strength, range flags, and peer support
   into labels such as **Normal**, **Drift**, **Contextual Anomaly**,
   **Drift + Anomaly**, **Strong Anomaly** (see script classify()).

8) **Dashboard** — same results layout as other testing tabs: plots, per-tag
   tables, methodology download.
"""

DOC_TESTING_FUSION_V7 = """
Testing (V7) — Tri-engine fusion (operations + process dynamics + multivariate)
================================================================================

Audience
--------
Built for a **combined lens** familiar to chemical / process engineers (limits,
clean periods, sustained deviation), control / software engineers (explicit
state machines and flags), and experienced data scientists (residual models,
correlation structure, false-positive tradeoffs).

What runs
---------
On one upload, the app executes **three independent detectors** already exposed
elsewhere in the product:

1. **Auto No Causal (Part4)** — auto clean window + per-tag std/MAD baseline +
   fixed z ladder (Drift / Drift + Anomaly / Strong Anomaly).

2. **Outlier detection** (multi-signal tab; former Testing V5) — clean period without moving average; clean-like limits;
   outside-limit paths plus **within-limit** spike, error-change, and persistent
   in-band deviation (run lengths).

3. **Outlier detection (using data model)** (former Testing V6) — top-5 Pearson-correlated tags per target, ridge prediction,
   **residual z**, quantile soft/outer bands, peer-shift context, and
   **Contextual Anomaly** when the value is consistent with historical range but
   inconsistent with peer-based prediction.

Fusion rule (default)
---------------------
For each **(Timestamp, Tag)** row:

- Map each engine’s **Final_Class** to an **ordinal severity**:
  Normal < Drift < Contextual Anomaly < Drift + Anomaly < Strong Anomaly.

- **Fused label** = the **maximum** severity among the three engines.

- If an engine has **no row** for that tag (e.g. V6 skips a tag when no
  predictors exist), that engine is treated as **Normal** for that row so the
  other engines still contribute.

Rationale: this is a **conservative union** for abnormal scenarios — any engine
seeing strong evidence pulls the fused label up. It favors **recall** on
compound faults (univariate + dynamics + relationship break) at the cost of
possibly more **Normal → Abnormal** upgrades when one engine is noisy.

Transparency
------------
Per-row **Fusion_Rationale** text is appended in detail tables (which engine(s)
drove the winning severity). Summary metrics include class histograms for each
engine plus the fused distribution.

Limits / related tags in the UI
--------------------------------
- **Tag limits**: prefer **V6** quantile-derived band when that tag appears in
  the V6 model; otherwise fall back to **Part4** symmetric z limits.

- **Related tags**: parsed from V6 **Top_Correlated_Features** when present;
  otherwise empty for that tag.

Performance note
----------------
V7 runs **all three** pipelines sequentially — expect roughly the sum of their
runtimes (dominated by V6 for many tags).
"""

DOCS: dict[str, tuple[str, str]] = {
    "part4": ("auto_no_causal_methodology.docx", DOC_AUTO_NO_CAUSAL),
    "part5": ("auto_no_clean_data_methodology.docx", DOC_AUTO_NO_CLEAN_DATA),
    "part6": ("auto_identification_methodology.docx", DOC_AUTO_IDENTIFICATION),
    "part7": ("auto_testing_deviation_spike_v4_methodology.docx", DOC_TESTING_DEVIATION_SPIKE_V4),
    "part8": ("auto_testing_deviation_spike_v5_methodology.docx", DOC_TESTING_DEVIATION_SPIKE_V5),
    "part9": ("auto_testing_top5_corr_regression_v6_methodology.docx", DOC_TESTING_TOP5_CORR_REGRESSION_V6),
    "part10": ("auto_testing_fusion_v7_methodology.docx", DOC_TESTING_FUSION_V7),
}
