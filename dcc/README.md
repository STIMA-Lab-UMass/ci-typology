[< Back to root](../README.md)

# ci-typology — pipeline guide

This directory contains the public pipeline: project setup, Overture
download/processing, and OpenAI NAICS classification.
For the full methodology and figures, see the source M.S. thesis at
[`../doc/anjali-thesis-2025.pdf`](../doc/anjali-thesis-2025.pdf).

## Directory structure

Each sub-directory has its own `readme.md` with more detail.

- **[data](data/readme.md)** — downloads and clips Overture data (read directly
  from the public Overture release bucket via DuckDB; no credentials).
- **[classification](classification/readme.md)** — classifies Overture features
  into NAICS codes (2/4/6-digit) using the OpenAI API, and labels the Overture
  data back with the result.
- **[project_setup](project_setup/)** — slices country admin layers from the
  global GADM geopackage and grids the boundary.
- **[utils](utils/readme.md)** — shared helpers (gridding, Parquet export,
  Overture version resolution, metadata, rate limiting, etc.).

## Top-level entry points

- `project_starter.py` — sets up GADM admin files and the processing grid for a
  country.
- `overture_data_processing.py` — downloads, clips, tiles, and filters Overture
  data.
- `classify_overture.py` — classifies Overture features into NAICS codes via the
  OpenAI API and labels the Overture data.

---

## Replicate the pipeline locally

Before you start, create the environment and `.env`, and complete the one-time
GADM download as described in the [root README](../README.md). Throughout,
`rwa_config_v0.yml` is an example per-country config name; substitute your own
ISO3 code and version.

### Step 1 — Download GADM and set up region grid cells

```bash
python3 dcc/project_starter.py
```

`project_starter.py` reads the user-supplied global GADM 4.1 geopackage at
`$PROJECT_DATA/GADM_global/gadm_410-levels.gpkg` (auto-unzipped from
`gadm_410-levels.zip` if needed) and sets up the files for your country of
interest. If the geopackage is missing, it prints the gadm.org download
walkthrough and exits — it never downloads from a private cloud bucket.

It prompts for:

- an **ISO3 country code** (e.g. `RWA`);
- a **sector / class** to focus on (all categories are listed in
  [`classification/overture_class.txt`](classification/overture_class.txt));
  choose `all` to keep every category;
- an **Overture release version**.

```text
Which sector are you interested in (Use up/down and press Enter):
> all
  industrial
  hospital
  school
  religious
  others
```

**Outputs:**
- `$PROJECT_DATA/<iso>/regions` — per-country GADM admin-layer files.
- `$PROJECT_DATA/<iso>_building_tiles/<country_name>_gridded` — the grid that
  divides the country into tiles for efficient processing.
- `envs/<iso>_config_v<n>.yml` — a per-country config (a new versioned file is
  generated each run).

### Step 2 — Download and process Overture data

```bash
python3 dcc/overture_data_processing.py --config_name rwa_config_v0.yml
```

Retrieves Overture data with DuckDB SQL queries directly from the public Overture
release bucket (`s3://overturemaps-us-west-2`, anonymous access), clips it to the
boundary grid, collects the unique attribute combinations used for
classification, and — if you selected a specific sector — filters the tiles to
that sector. See [`data/readme.md`](data/readme.md) for details.

**Outputs:**
- `$PROJECT_DATA/overture_tiled/<iso>_<version>` — downloaded Overture data
  (`bbox` folder) clipped to the boundary grid (`clipped` folder).
- `$PROJECT_OUT/add_<iso>_<version>/combined_classes` — CSVs collating the unique
  metadata for classification.

### Step 3 — Classify Overture data points into NAICS codes

```bash
python3 dcc/classify_overture.py --config_name rwa_config_v0.yml
```

Classifies Overture features into NAICS categories with the OpenAI API. The
pipeline first assigns 4-digit NAICS codes and, if enabled in the config, then
assigns 6-digit codes. The OpenAI model is selected interactively and saved to
[`classification/selected_model.json`](classification/selected_model.json);
options are `gpt-4o`, `gpt-4o-mini`, `gpt-5`, `gpt-5-mini` (default `gpt-5`). This
step requires `OPEN_AI_API_KEY` and network access.

#### Administrative filtering (optional)

You can limit classification to a specific administrative value (e.g. a single
state) to reduce processing time and cost. During the interactive setup you'll be
prompted:

```text
Do you want to filter classification by administrative boundaries? (y/N): y
Available administrative columns: ['NAME_1']
Enter the administrative column name: NAME_1
Enter the value to filter by (e.g., 'Maryland'): Maryland
```

Only data within the chosen boundary is sent to the LLM. The available columns
depend on the `admin_columns` setting in your per-country config. See
[`classification/readme.md`](classification/readme.md) for more.

**Outputs:**
- `$PROJECT_OUT/add_<iso>_<version>/combined_classes` — 4-digit classified files
  ending in `*_classified.csv` (and 6-digit files ending in
  `*_naics_6_classified.csv`, if enabled).
- `$PROJECT_DATA/overture_tiled/<iso>_<version>` — labelled Overture `.parquet`
  files under a `<class>_labeled` folder. Optionally exportable to a `.gpkg`.
