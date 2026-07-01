[< Back to main](../README.md)

# utils

Shared helper functions used across the pipeline.

## Files

- **[grid_poly_shape.py](grid_poly_shape.py)** — creates and manipulates polygon
  grids for tile-based spatial processing.
- **[utils_vector.py](utils_vector.py)** — vector helpers (e.g. building the
  vector grid, bounding boxes).
- **[overture_version.py](overture_version.py)** — resolves the selected Overture
  release version (including `latest`) against the public Overture release
  listing.
- **[subset_helper.py](subset_helper.py)** — resolves the optional ADM-region
  subset context and the scoped output/tile paths.
- **[export_parquet.py](export_parquet.py)** — merges and exports labelled Parquet
  tiles to GeoPackage.
- **[metadata_helper.py](metadata_helper.py)** — manages run/progress metadata.
- **[rate_limiter.py](rate_limiter.py)** — throttles OpenAI API request rates.
- **[util_cursor.py](util_cursor.py)** — small terminal-cursor helpers for the
  interactive prompts.
