# chapkit-ewars-template

> WHO EWARS Bayesian INLA disease-forecasting model, wrapped as a [chapkit](https://github.com/dhis2-chap/chapkit) service and ready to plug into [chap-core](https://github.com/dhis2-chap/chap-core).

[![CI](https://github.com/chap-models/chapkit_ewars_template/actions/workflows/ci.yml/badge.svg)](https://github.com/chap-models/chapkit_ewars_template/actions/workflows/ci.yml)
[![Publish Docker](https://github.com/chap-models/chapkit_ewars_template/actions/workflows/publish-docker.yml/badge.svg)](https://github.com/chap-models/chapkit_ewars_template/actions/workflows/publish-docker.yml)
[![GHCR](https://img.shields.io/badge/ghcr.io-chap--models%2Fchapkit__ewars__template-blue?logo=docker)](https://github.com/chap-models/chapkit_ewars_template/pkgs/container/chapkit_ewars_template)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)
[![License: GPLv3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![powered by chapkit](https://img.shields.io/badge/powered%20by-chapkit-8A2BE2)](https://github.com/dhis2-chap/chapkit)

## Overview

This repo packages a modified version of the WHO EWARS (Early Warning, Alert and Response System) model — a hierarchical Bayesian disease-case forecaster built on [R-INLA](https://www.r-inla.org/) with distributed-lag climate covariates via [`dlnm`](https://cran.r-project.org/package=dlnm) — as a **chapkit FastAPI service**. It is intended as a drop-in chap-core model template and as a reference for anyone wrapping an R/Python model with chapkit.

Unlike the older `MLproject`-based templates, there is no YAML config and no external adapter file. chap-core discovers the service via `GET /api/v1/info`, and all chapkit wiring lives in a single [`main.py`](main.py). Column adapting to the model's internal names happens inside the R code.

**Inputs:** monthly (or weekly) CSVs with `time_period, rainfall, mean_temperature, disease_cases, population, location`.
**Outputs:** a CSV of 1000 posterior samples per forecast row (`sample_0 .. sample_999`).

## Quickstart — run the prebuilt image

Pull and run the latest image from GHCR:

```bash
docker compose -f compose.ghcr.yml up
```

The service will be on `http://localhost:8000`:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/info
```

## Build and run locally

```bash
make build      # docker build
make run        # build + run on :8000
```

> R-INLA is **amd64-only**, so the Dockerfile pins `linux/amd64` on both stages (see [`Dockerfile`](Dockerfile)). Builds on Apple Silicon work but run under emulation.

## How it integrates with chap-core

chap-core auto-detects chapkit services and drives them over REST — no config file, no entry-point YAML. All the integration contract lives in [`main.py`](main.py):

| What | Where | Current value |
| --- | --- | --- |
| Service id | `MLServiceInfo.id` | `ewars-template` |
| Period type | `MLServiceInfo.period_type` | `monthly` |
| Required covariates | `MLServiceInfo.required_covariates` | `["population"]` |
| Free continuous covariates | `allow_free_additional_continuous_covariates` | `True` |
| Prediction-period bounds | `min/max_prediction_periods` | `0 – 100` |
| Train command | `ShellModelRunner.train_command` | `Rscript scripts/train.R --data {data_file}` |
| Predict command | `ShellModelRunner.predict_command` | `Rscript scripts/predict.R --historic {historic_file} --future {future_file} --output {output_file}` |

Runtime-tunable config (`EwarsConfig`):

| Field | Default | Description |
| --- | --- | --- |
| `prediction_periods` | `3` | Periods to forecast |
| `n_lags` | `3` | Lags in the `dlnm` cross-basis |
| `precision` | `0.01` | Prior precision on fixed effects (regularization) |

Point chap-core at either the GHCR image or a locally-built one; it will handle the train/predict round-trips and surface the posterior samples as the model's output.

## Data contract

chap-core delivers these canonical columns; the R code derives everything else.

**Historic / training data** (`example_data_monthly/training_data.csv`):

| Column | Type | Notes |
| --- | --- | --- |
| `time_period` | string | e.g. `2023-05` |
| `rainfall` | float | |
| `mean_temperature` | float | |
| `disease_cases` | int | target |
| `population` | int | used as offset |
| `location` | string | spatial id |

**Future data**: identical shape, with `disease_cases` missing or `NA`. `scripts/predict.R` row-binds historic + future, re-fits INLA on the combined frame, and samples from the posterior for the NA rows.

**Output** (`predictions.csv`):

```csv
time_period,location,sample_0,sample_1,...,sample_999
2024-01,O6uvpzGd5pu,87,164,62,...,70
```

The derived columns the model code uses internally (`Cases`, `E`, `ID_spat`, `ID_year`, `rainsum`, `meantemperature`, `week`/`month`) are produced by `apply_adapters()` in [`scripts/predict.R`](scripts/predict.R) — callers do not need to supply them.

## Model

Negative-binomial INLA with an `iid` spatial random effect on `ID_spat`, an RW1 temporal trend, a cyclic RW1 on `ID_time_cyclic` (week or month), and `dlnm` cross-basis splines for lagged `rainsum` and `meantemperature`. `population` enters as a log offset. The fit runs in [`scripts/predict.R`](scripts/predict.R); [`scripts/train.R`](scripts/train.R) is a placeholder because the INLA fit needs the combined historic+future frame available at predict time.

**Weekly vs. monthly** — the only thing that changes between the two is the lag window and the cyclic index:

```r
if ("week" %in% colnames(df)) {
  nlag <- 12                                 # ~3 months of weekly lags
  df <- mutate(df, ID_time_cyclic = week)
  df <- offset_years_and_weeks(df)
} else {
  nlag <- 3                                  # ~3 months of monthly lags
  df <- mutate(df, ID_time_cyclic = month)
  df <- offset_years_and_months(df)
}
```

Weekly data lives in `example_data/`, monthly in `example_data_monthly/`.

## Repository layout

```
.
├── main.py                      # chapkit service definition (MLServiceBuilder, ShellModelRunner)
├── scripts/
│   ├── train.R                  # placeholder — validates input, INLA fit happens at predict time
│   ├── predict.R                # adapter + INLA model + posterior sampling
│   └── lib.R                    # cyclic week/month offset helpers
├── example_data/                # weekly demo CSVs
├── example_data_monthly/        # monthly demo CSVs
├── Dockerfile                   # two-stage: uv builder + dhis2-chap/docker_r_inla runtime
├── compose.yml                  # build locally and run
├── compose.ghcr.yml             # pull prebuilt image from GHCR
├── Makefile                     # build / run
└── .github/workflows/           # CI (lint + docker build) + GHCR publish
```

## Using this repo as a template for your own chapkit model

1. Fork this repository (or use the GitHub "Use this template" button once enabled on the fork).
2. Rename the package in [`pyproject.toml`](pyproject.toml).
3. Update `MLServiceInfo` in `main.py` — set your own `id`, `display_name`, `required_covariates`, and `period_type`.
4. Replace `scripts/*.R` with your own model (or rewrite `main.py` to use chapkit's Python runner instead of `ShellModelRunner`).
5. Push to `main`. The `publish-docker` workflow will build and push an image to `ghcr.io/<your-org>/<your-repo>:latest` automatically — no secrets to configure.

## License

[GPL v3](LICENSE).

## Related

- [chapkit](https://github.com/dhis2-chap/chapkit) — the FastAPI ML-service framework this template builds on
- [servicekit](https://github.com/winterop-com/servicekit) — the lower-level async service framework chapkit is built on
- [chap-core](https://github.com/dhis2-chap/chap-core) — the CHAP platform that consumes this model
- [chap-models](https://github.com/chap-models) — sibling model templates in the CHAP ecosystem
- [WHO EWARS](https://www.who.int/activities/early-warning-alert-and-response-network) — upstream program
- [R-INLA](https://www.r-inla.org/) and [`dlnm`](https://cran.r-project.org/package=dlnm) — the statistical machinery under the hood
