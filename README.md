# ci-typology

**Commercial & industrial building typology from open geospatial data, via LLM NAICS classification.**

`ci-typology` builds a standardized, NAICS-classified picture of the built
environment from *public* geospatial data. It downloads
[Overture Maps](https://overturemaps.org/) features (building footprints, points
of interest, and land-use "bases") and classifies each feature into a
[NAICS](https://www.census.gov/naics/) category with the OpenAI API — producing a
map in which every Overture feature carries an LLM-inferred industry
classification. This classification layer is the foundation for downstream,
bottom-up electricity-demand analysis.

> **Method (one paragraph).** A country boundary from
> [GADM](https://gadm.org/) defines the study area, which is tiled into a
> configurable grid (25 km × 25 km by default) so tiles can be processed
> independently. For each tile, Overture features are streamed from the public
> Overture release bucket. Because Overture's raw attributes (names, subtypes,
> categories) are free-text and inconsistent, an LLM (OpenAI API) maps them into
> the hierarchical NAICS schema — first a residential/non-residential split, then
> a 4-digit (optionally 6-digit) NAICS code — labelling each Overture feature with
> its inferred industry classification.

**Full documentation:** the methodology, data sources, and limitations are
described in the source M.S. thesis,
**[`doc/anjali-thesis-2025.pdf`](doc/anjali-thesis-2025.pdf)**.

This public release implements the three stages above (GADM setup, Overture
download/processing, and NAICS classification). The downstream framework in the
source thesis (the building-footprint join, meter-to-building joins,
building-volume estimation, and consumption modelling) requires user-supplied or
private data and is **out of scope** here.

---

## 1. Install

The project ships a conda/mamba environment (`ci-typology`, Python 3.9):

```bash
mamba env create -f envs/environment.yml
conda activate ci-typology
```

## 2. Configure

Create a `.env` file in the repository root from the template and fill in your
own values:

```bash
cp envs/.env_template .env
```

`.env` defines five variables, all read via `os.environ`:

```bash
PROJECT_ROOT=<absolute path to the cloned repo>
PROJECT_DATA=<absolute path for input data (GADM_global, building tiles, ...)>
PROJECT_OUT=<absolute path where pipeline outputs are written>
PROJECT_CACHE_METADATA=<absolute path for run / progress metadata>
OPEN_AI_API_KEY=<your OpenAI API key>   # note the non-standard variable name
```

Other configuration:

- **Master config** `envs/country_config.yml` holds the shared settings: the GADM
  admin levels, the selectable Overture release versions, the sector/class
  catalogue, the ISO3→country lookup, and the NAICS code dictionaries. Running
  the setup step (below) generates a per-country config
  `envs/<iso>_config_v<n>.yml` with country-specific keys.
- **OpenAI model** is chosen interactively during classification and saved to
  `dcc/classification/selected_model.json`. Options: `gpt-4o`, `gpt-4o-mini`,
  `gpt-5`, `gpt-5-mini` (default `gpt-5`). The `gpt-4o` family uses deterministic
  decoding (`seed=42, temperature=0.1, top_p=0.1`).
- **Overture version** is chosen during setup and stored in the per-country
  config. Browse versions at <https://docs.overturemaps.org/release/>.

## 3. One-time data setup

### GADM administrative boundaries (manual, one-time)

The pipeline needs the global **GADM 4.1** boundary geopackage; it slices each
country's admin layers (`ADM_0`…`ADM_5`) from it.

1. Open <https://gadm.org/download_world.html>.
2. Under **"the entire world"**, choose the **Geopackage** format, the
   **"six separate layers"** variant. This downloads **`gadm_410-levels.zip`**
   (~1 GB; contains layers `ADM_0` through `ADM_5`).
   *Do not use the single-layer `gadm_410.gpkg` — the code expects the
   `-levels` geopackage.*
3. Create a `GADM_global` folder inside your `PROJECT_DATA` directory and move
   the zip there (the pipeline unzips it automatically):

   ```bash
   mkdir -p "$PROJECT_DATA/GADM_global"
   mv ~/Downloads/gadm_410-levels.zip "$PROJECT_DATA/GADM_global/"
   ```

   Expected layout (either the zip or the unzipped gpkg is fine):

   ```
   $PROJECT_DATA/GADM_global/gadm_410-levels.zip     # pipeline unzips this ->
   $PROJECT_DATA/GADM_global/gadm_410-levels.gpkg
   ```

If the file is missing when you run the setup, the pipeline prints these exact
steps and exits — it never attempts a network or cloud download.

### Overture Maps (already public — no credentials)

Overture data is read directly from the anonymous public release bucket
`s3://overturemaps-us-west-2` via DuckDB's `httpfs` extension. There is **no
private mirror and no key to configure** — you only pick a release version
(above). The download script installs the DuckDB `httpfs` extension
automatically; you just need DuckDB and internet access.

## 4. Run the pipeline

The three steps below use only public resources. `rwa_config_v0.yml` is an example
per-country config name (ISO3 `RWA`, version `0`); substitute your own.

```bash
# Step 1 — project setup: slice the country's GADM admin layers and grid them.
#          Prompts for an ISO3 code (e.g. RWA), a sector/class, and an Overture
#          version; writes envs/<iso>_config_v<n>.yml.
python3 dcc/project_starter.py

# Step 2 — download and process Overture data (clip to the boundary grid).
python3 dcc/overture_data_processing.py --config_name rwa_config_v0.yml

# Step 3 — classify Overture features into NAICS codes via the OpenAI API.
#          (Requires OPEN_AI_API_KEY and network access.)
python3 dcc/classify_overture.py --config_name rwa_config_v0.yml
```

**Inputs:** the GADM geopackage (above), an OpenAI API key, and internet access.
**Outputs:** per-country GADM admin files and grids; clipped Overture Parquet
tiles; and NAICS-classified CSVs (4-digit, optional 6-digit) and labelled Overture
files under `PROJECT_OUT`.

## 5. Verify your setup / Testing

The repository ships an offline-safe test suite. After creating the environment:

```bash
mamba env create -f envs/environment.yml
conda activate ci-typology
pytest -q          # runs the offline test suite
```

The suite checks that every surviving module imports cleanly, that the
configuration and `.env` template are coherent, that the GADM-missing-file path
prints the download instructions and exits without a cloud download, that each
command-line entry point parses its arguments, and that no secrets are present in
the tree. Tests that would require the OpenAI API or network are **skipped
automatically** (not failed) when credentials or connectivity are absent, so the
suite runs green offline.

---

## License

Released under the **MIT License** — see [`LICENSE`](LICENSE).
Copyright © UMass Amherst.

## Citation

The methodology implemented here is described in Anjali's M.S. thesis. If you use
this software or its methodology, please cite both the thesis and this repository:

> Anjali. *Bottom-Up Systems for Electricity Consumption Estimation at Scale.*
> M.S. Thesis, Department of Computer Science, University of Massachusetts
> Amherst, May 2025.

> Anjali Anjali, Stephen J. Lee, and Jay Taneja. *ci-typology: commercial &
> industrial building typology from open geospatial data.* STIMA Lab,
> University of Massachusetts Amherst, 2026.
> <https://github.com/STIMA-Lab-UMass/ci-typology>.

## Acknowledgments

This work was funded by **[Project InnerSpace](https://projectinnerspace.org/)**,
and we are deeply grateful for their support.

The classification pipeline depends on the **[OpenAI API](https://openai.com/)** to
map Overture features into standardized NAICS categories; we thank OpenAI for
access to their API.

This work builds on methodology developed under the guidance of Prof. Jay Taneja
and Dr. Stephen Lee, and relies on several open datasets, including those from the
[Overture Maps Foundation](https://overturemaps.org/), [GADM](https://gadm.org/),
[Google Open Buildings](https://sites.research.google/open-buildings/), and
[Microsoft Global ML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints).
We gratefully acknowledge these data providers and the open-source geospatial
community.
