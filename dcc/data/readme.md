[< Back to main](../readme.md)
## Overview
This package contains the scripts that pull and prepare Overture Maps data for the
pipeline. Overture data is read directly from the public release bucket
(`s3://overturemaps-us-west-2`) via DuckDB's `httpfs` extension - no credentials are
required, you only pick a release version.

## Files
- **[download_clip_overture_v01.py](download_clip_overture_v01.py)**
This file downloads Overture data for all the gridded bounding boxes.

- **[grid_overture_data_v01.py](grid_overture_data_v01.py)**
This file clips the Overture bounding boxes onto the gridded shape and removes the
points which are not within the boundary of interest.

- **[list_ot_raw_classes.py](list_ot_raw_classes.py)**
It lists all the unique attributes which help to classify the data into categories and
stores them in a CSV file.

- **[filter_overture_class.py](filter_overture_class.py)**
It filters the gridded Overture tiles down to a selected sector (e.g. `industrial`,
`hospital`, `school`) based on the `classification.class` value in the country config.
