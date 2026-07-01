[< Back to main](../README.md)

# classification

Classifies Overture features into NAICS codes using the OpenAI API, then labels
the Overture data back with the assigned codes. Run via
[`../classify_overture.py`](../classify_overture.py).

## Files

- **[label_overture_v01.py](label_overture_v01.py)** — labels the Overture
  tiles back with the NAICS codes produced by the LLM classification.
- **[model_config.py](model_config.py)** — selects and persists the OpenAI model
  (`gpt-4o`, `gpt-4o-mini`, `gpt-5`, `gpt-5-mini`; default `gpt-5`) and its
  decoding parameters. The choice is saved to
  [`selected_model.json`](selected_model.json).
- The OpenAI classification stages — each builds a prompt, calls the OpenAI API,
  parses the response, and stores the result as CSV:
  - **[openai_naics_4_classification_res_non_res.py](openai_naics_4_classification_res_non_res.py)**
    — binary residential / non-residential split, then 4-digit NAICS.
  - **[openai_naics_4_classification.py](openai_naics_4_classification.py)**
    — 4-digit NAICS classification.
  - **[openai_naics_6_classification.py](openai_naics_6_classification.py)**
    — optional 6-digit NAICS refinement.
  - **[openai_naics_2_classification.py](openai_naics_2_classification.py)**
    — 2-digit NAICS classification.
- **[overture_class.txt](overture_class.txt)** — the catalogue of selectable
  Overture sectors / classes.
- **[selected_model.json](selected_model.json)** — the currently selected OpenAI
  model (no secrets).

## Run

```bash
python3 dcc/classify_overture.py --config_name <iso>_config_v<n>.yml
```

This checks for existing classification CSVs and, where they are missing, calls
the OpenAI API to generate them. It requires `OPEN_AI_API_KEY` and network access.
