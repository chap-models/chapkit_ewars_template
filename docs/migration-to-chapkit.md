# Migrating an ML model repo to chapkit

This guide walks through converting a standalone ML model (e.g. an R model driven by MLflow or a handful of scripts) into a chapkit-based service that CHAP can discover, configure, train, and query over HTTP.

The recommended path is to scaffold a fresh chapkit project with the `chapkit` CLI and then port your model code into it. That is what this guide covers.

## Worked example: `ewars_template` to `chapkit_ewars_template`

The concrete before/after used throughout this guide:

- **Starting point:** [`chap-models/ewars_template`](https://github.com/chap-models/ewars_template) `main` branch — a pre-chapkit R model driven by MLflow. Flat repo with `MLproject`, `train.R`, `predict.R`, `lib.R`, `isolated_run.R`, `example_config.yaml`, `pure_config.yaml`, and pre-computed example outputs under `example_data/` and `example_data_monthly/`.
- **Ending point:** [`chap-models/chapkit_ewars_template`](https://github.com/chap-models/chapkit_ewars_template) — the migrated service. A 77-line `main.py`, R scripts moved into `scripts/` with named CLI flags and a column adapter, a Python + uv Dockerfile layered on an R-INLA base image, and CI that runs `chapkit test` against the built container.

Every file path and code snippet in this guide is lifted verbatim from the ending-point repository.

## Universal vs model-specific changes

A migration has three kinds of changes. Knowing which is which saves time:

1. **Universal — every migration needs these.** Replacing orchestration (MLproject, isolated runners) with chapkit's `main.py`. Named CLI flags on your scripts. `config.yml` parsing. Layering Python + uv on top of your modeling base image. Deleting the old orchestration and committed example outputs.
2. **R-specific — needed for any R (or non-Python) model.** Adding a column adapter function in your script because chapkit does not apply MLflow-style `adapters:` maps. Replacing the scaffolded Python-only Dockerfile with one based on an R image.
3. **Model-specific — needed only if your model needs it.** EWARS-specific examples: robust `time_period` parsing, weekly vs monthly dispatch, offsetting years/weeks. If your original predict script already handled these things cleanly, leave them alone. Do not copy EWARS's fixes into an unrelated model.

The guide flags each section with which category it falls into.

## Guide structure

- **Part A — Required.** The minimum set of steps to produce a functioning chapkit service. Skip nothing here.
- **Part B — Optional.** Lint, CI, compose, docs, and similar polish. Add as you see fit.

A manual scaffolding appendix is included at the end for situations where you cannot use the CLI.

---

## What chapkit gives you

Before changing anything, it helps to know what you get in return.

A running chapkit service exposes these endpoints out of the box — you do not write any route code:

- `GET /health` — health check, used by CI and CHAP.
- `GET /api/v1/info` — service metadata (id, covariates, period type, prediction bounds).
- `GET /api/v1/configs/$schema` — JSON schema of your config class.
- `POST /api/v1/configs` — create a config for a run.
- `POST /api/v1/ml/$train` — submit a training job.
- `POST /api/v1/ml/$predict` — submit a prediction job.
- `GET /api/v1/ml/$status` — poll job status.
- `GET /api/v1/artifacts` — retrieve artifacts.

You also get:

- A SQLite-backed job and artifact store (default path `data/chapkit.db`).
- Pydantic validation of every config the service receives.
- A `chapkit test` CLI that drives the service end-to-end over HTTP — used for local smoke tests and CI.
- A `chapkit init` CLI that scaffolds the entire project layout (main.py, Dockerfile, compose, scripts/, README, Postman collection) in one command.

What chapkit does **not** provide: your modeling code, your R or Python base image, or your data adapters. Those stay with you.

---

## Prerequisites

- A working model that can train and predict as standalone scripts (R, Python, or any shell command).
- Python 3.13 and [uv](https://github.com/astral-sh/uv).
- Docker.
- Familiarity with the CHAP canonical column names: `disease_cases`, `population`, `location`, `time_period`, and optional continuous covariates like `rainfall`, `mean_temperature`.

---

# Part A — Required

After completing Part A you can run `uvicorn main:app` locally, hit `/health`, and pass `chapkit test`.

## A.1 Install the chapkit CLI

Install chapkit as a global uv tool so `chapkit` is available on your `PATH`:

```
uv tool install chapkit
```

Upgrade later with:

```
uv tool upgrade chapkit
```

Verify:

```
chapkit --version
chapkit --help
```

You should see `init` and `artifact` as subcommands when you run `chapkit --help` outside a chapkit project. Inside a chapkit project, `init` is hidden and `test` is shown instead — more on that below.

## A.2 Scaffold a new project with `chapkit init`

> **Important: run `chapkit init` from *outside* any existing chapkit project.** The CLI walks up from the current directory looking for a `pyproject.toml` that depends on `chapkit`. If it finds one, the `init` command is hidden entirely and you will only see `test` and `artifact`. Move to a parent directory first — for example `cd ~/dev` or `cd /tmp` — before running init.

Run:

```
chapkit init my-model --template ml-shell
```

### Template options

The `--template` flag picks the scaffold shape:

| Template | Use when |
|---|---|
| `ml` *(default)* | Model logic lives inline in `main.py` as Python functions. Fine for pure-Python models with no external script dependencies. |
| `ml-shell` | Model logic lives in external scripts invoked via `ShellModelRunner`. **Use this for R, Julia, or any non-Python model.** This is the template `chapkit_ewars_template` is based on. |
| `task` | Generic task runner, not a machine-learning service. Ignore for model migrations. |

For migrating an existing R (or other non-Python) model, `--template ml-shell` is the right choice.

### Optional flags

- `--path <dir>` — target parent directory (default: current directory).
- `--with-monitoring` — also generate a Prometheus + Grafana monitoring stack under `monitoring/` and a compose file that wires it up.

### What the scaffold generates

For `--template ml-shell` you will get something like:

```
my-model/
├── main.py                  # chapkit service definition, ready to customize
├── pyproject.toml           # pinned chapkit dep
├── Dockerfile               # Python 3.13 + uv base image
├── compose.yml              # local build + run
├── README.md                # per-project quickstart
├── postman_collection.json  # importable API collection
├── .gitignore
└── scripts/
    ├── train_model.py       # Python placeholder train script
    └── predict_model.py     # Python placeholder predict script
```

Then:

```
cd my-model
uv sync
uv run python main.py
```

The skeleton should boot and serve on `:8000`. Hit `http://localhost:8000/health` to confirm.

At this point you have a working chapkit service using placeholder scripts. The rest of Part A is customizing the scaffold to your model.

## A.3 Customize `main.py`

The scaffold's `main.py` already wires up imports, `ShellModelRunner`, `ArtifactHierarchy`, and `MLServiceBuilder`. You only need to edit four things: config fields, command templates, service info, and (optionally) the hierarchy name. Each is walked through below, with concrete examples from `chapkit_ewars_template`.

### Scaffold extras you can trim

The generated `main.py` also ships with some things the reference `chapkit_ewars_template/main.py` does not have. Most are safe to delete or keep as taste dictates:

- A module-level docstring (`"""ML service for <project-name>."""`).
- A `if __name__ == "__main__":` runner that calls `run_app("main:app", reload=False)`, allowing `python main.py` as an alternative to `uvicorn main:app`.
- Explanatory comment blocks above the runner and command templates.

The reference repo includes `.with_registration(keepalive_interval=15)` in the builder chain — registration is controlled by env vars at runtime, so there is nothing to uncomment. See section B.8 for the full story.

The reference repo keeps `main.py` at ~100 lines. You do not have to match that.

### A.3.1 Config class

Find the `Config` class in `main.py` and replace its fields with your model's tunable parameters. Subclass stays `BaseConfig`. Use `Field(default=..., description=...)` so the generated schema is self-documenting:

```python
from pydantic import Field

class EwarsConfig(BaseConfig):
    prediction_periods: int = Field(
        default=3,
        description="Number of periods to predict into the future",
    )
    n_lags: int = Field(
        default=3,
        description="Number of lags to include in the model",
    )
    precision: float = Field(
        default=0.01,
        description="Prior on the precision of fixed effects. Works as regularization",
    )
    region_seasonal: bool = Field(
        default=False,
        description="Optional inclusion of region specific seasonal effects",
    )
    # BaseConfig reserves `additional_continuous_covariates` — override the
    # default here so EWARS uses rainfall + mean_temperature out of the box.
    # Deployments can override per-config via POST /api/v1/configs.
    additional_continuous_covariates: list[str] = Field(
        default_factory=lambda: ["rainfall", "mean_temperature"],
        description="Continuous covariates for the lagged INLA model",
    )
```

Every field here replaces what used to live in a flat YAML config file. Validation and defaults now live in Python, and the schema is auto-exposed at `/api/v1/configs/$schema`. Note that `additional_continuous_covariates` is a reserved `BaseConfig` field — overriding its default here means the EWARS model includes climate covariates by default, but deployments can create variant configs with a different set (e.g. `[]` for population-only) via `POST /api/v1/configs`.

### A.3.2 Shell runner command templates

Edit the `train_command` and `predict_command` strings on the `ShellModelRunner`. Replace the Python placeholders with the commands for your model. For an R model:

```python
runner: ShellModelRunner[EwarsConfig] = ShellModelRunner(
    train_command="Rscript scripts/train.R --data {data_file}",
    predict_command=(
        "Rscript scripts/predict.R --historic {historic_file} --future {future_file} --output {output_file}"
    ),
)
```

Available placeholders chapkit substitutes at runtime:

- `{data_file}` — training input CSV (train only).
- `{historic_file}` — historical input CSV (predict only).
- `{future_file}` — future input CSV to predict over (predict only).
- `{output_file}` — path the script must write predictions to (predict only).
- `{geo_file}` — optional GeoJSON, when the request includes geometry.

Chapkit also writes a `config.yml` file into the working directory before invoking the script — more on that in A.4.

### A.3.3 Service metadata

`MLServiceInfo` is what CHAP uses to discover your service. Be honest about covariates and bounds — CHAP validates requests against them.

```python
info = MLServiceInfo(
    id="chapkit-ewars-template",
    display_name="CHAP-EWARS Model (chapkit)",
    version="1.0.0",
    description=(
        "Modified version of the World Health Organization (WHO) EWARS model. "
        "EWARS is a Bayesian hierarchical model implemented with the INLA library."
    ),
    model_metadata=ModelMetadata(
        author="CHAP team",
        author_assessed_status=AssessedStatus.orange,
        organization="HISP Centre, University of Oslo",
        organization_logo_url="https://landportal.org/sites/default/files/2024-03/university_of_oslo_logo.png",
        contact_email="knut.rand@dhis2.org",
        citation_info="...",
    ),
    period_type=PeriodType.monthly,
    allow_free_additional_continuous_covariates=True,
    required_covariates=["population"],
    min_prediction_periods=0,
    max_prediction_periods=100,
)
```

Fields you almost always need to customize:

- `id` — unique service identifier. Lowercase, hyphenated.
- `display_name`, `version`, `description` — human-readable metadata.
- `model_metadata` — author, organization, assessed confidence, citation.
- `period_type` — `PeriodType.monthly` or `PeriodType.weekly`.
- `required_covariates` — list of canonical CHAP column names your model requires in addition to `disease_cases`.
- `allow_free_additional_continuous_covariates` — whether the model can accept extra continuous covariates beyond those required.
- `min_prediction_periods`, `max_prediction_periods` — forecast horizon bounds.

### A.3.4 Artifact hierarchy (usually no edit needed)

The scaffold ships a reasonable default:

```python
hierarchy = ArtifactHierarchy(
    name="ewars",
    level_labels={0: "ml_training_workspace", 1: "ml_prediction"},
)
```

You can rename `name` and relabel levels if your model uses a different mental model for artifact organization, but in most cases the default is fine.

### A.3.5 Database and builder (usually no edit needed)

The scaffold already wires `MLServiceBuilder` and the SQLite database path:

```python
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/chapkit.db")
if DATABASE_URL.startswith("sqlite") and ":///" in DATABASE_URL:
    db_path = Path(DATABASE_URL.split("///")[1])
    db_path.parent.mkdir(parents=True, exist_ok=True)

app = (
    MLServiceBuilder(
        info=info,
        config_schema=EwarsConfig,
        hierarchy=hierarchy,
        runner=runner,
        database_url=DATABASE_URL,
    )
    .with_registration(keepalive_interval=15)
    .build()
)
```

`.with_registration()` enables self-registration with chap-core on startup via the `SERVICEKIT_ORCHESTRATOR_URL` environment variable. When unset, registration is skipped and the service runs standalone. See section B.8 for details.

Leave the rest untouched unless you need a different persistence backend.

## A.4 Replace the placeholder scripts with your model code

The `ml-shell` scaffold generates `scripts/train_model.py` and `scripts/predict_model.py` as Python placeholders. Delete them and drop in your real training and prediction scripts. They can be in any language — just make sure your `train_command` / `predict_command` strings in `main.py` match whatever you invoke them with.

For an R model you end up with something like:

```
scripts/
├── train.R
├── predict.R
└── lib.R    # shared helpers
```

Whatever the language, your scripts must follow chapkit's shell contract. There are five requirements. The first four are universal — every migration needs them. The fifth (`source()` paths) only applies if your scripts used to sit at the repo root.

> **A note on scope.** The examples below are lifted from `chapkit_ewars_template`, but not every edit EWARS needed applies to every model. EWARS also has model-specific predict-script changes — robust `time_period` parsing, weekly vs monthly dispatch, year offsetting — that were added during *its* migration. If your original predict script already handles these things cleanly, leave them alone. Copy only the universal changes, then touch your model code as little as possible.

### A.4.1 Parse named CLI flags

The flag names must match the placeholders in your command templates. For `train.R` this repo uses a minimal hand-rolled parser (`scripts/train.R:6-18`):

```r
args <- commandArgs(trailingOnly = TRUE)

data_file <- NULL
for (i in seq_along(args)) {
  if (args[i] == "--data" && i < length(args)) {
    data_file <- args[i + 1]
  }
}

if (is.null(data_file)) {
  data_file <- "data.csv"
}
```

`optparse` works just as well. The key point is: no positional arguments, no hardcoded paths.

### A.4.2 Read `config.yml` from the working directory

Chapkit serializes the validated Pydantic config instance to `config.yml` as flat YAML before running your script. Keys map 1:1 to your `BaseConfig` field names, so `n_lags: 3` reads back as `config$n_lags` in R. See `scripts/predict.R:29-47`:

```r
parse_config <- function(config_path) {
  if (!file.exists(config_path)) {
    return(list(
      n_lags = 3,
      precision = 0.01,
      additional_continuous_covariates = character()
    ))
  }
  config <- yaml.load_file(config_path)
  list(
    n_lags = if (!is.null(config$n_lags)) config$n_lags else 3,
    precision = if (!is.null(config$precision)) config$precision else 0.01,
    additional_continuous_covariates = if (!is.null(config$additional_continuous_covariates)) {
      config$additional_continuous_covariates
    } else {
      character()
    }
  )
}
```

Always provide sane fallbacks — if chapkit ever invokes the script directly without writing a config (e.g. during local debugging) it should still run.

### A.4.3 Apply a column adapter

CHAP speaks canonical column names (`disease_cases`, `population`, `location`, `year`) but your existing model code probably speaks something else (`Cases`, `E`, `ID_spat`, `ID_year`). Rather than rewrite the model, add a thin rename layer at the top of `predict.R`. See `scripts/predict.R:12-26`:

```r
apply_adapters <- function(df) {
  rename_map <- c(
    "disease_cases" = "Cases",
    "population" = "E",
    "location" = "ID_spat",
    "year" = "ID_year"
  )
  for (from in names(rename_map)) {
    to <- rename_map[[from]]
    if (from %in% colnames(df) && !(to %in% colnames(df))) {
      names(df)[names(df) == from] <- to
    }
  }
  return(df)
}
```

Call `apply_adapters()` on every dataframe you read from `{historic_file}` or `{future_file}`. Your existing modeling code stays untouched below that line.

### A.4.4 Handle training as a no-op (when applicable)

Some models — EWARS included — fit at predict time rather than train time. If that is the case for your model, `train.R` can just validate inputs and write a placeholder. See `scripts/train.R:27-35`:

```r
cat("Reading training data from:", data_file, "\n")
df <- read.csv(data_file)
cat("Training data shape:", nrow(df), "rows x", ncol(df), "columns\n")

saveRDS(list(trained = TRUE, n_rows = nrow(df)), file = "model.rds")
```

If your model has a real train phase, keep it — chapkit does not care whether training is expensive or trivial. Just make sure the script reads `{data_file}` and terminates successfully.

### A.4.5 Update `source()` paths if scripts moved

If your original scripts lived at the repo root and used `source("lib.R")` to load helpers, the move into `scripts/` breaks that call. Update every `source()` to the new relative path:

```r
# Before
source("lib.R")

# After
source("scripts/lib.R")
```

Chapkit runs your scripts with the project root as the working directory, so paths are relative to the project root, not to the script file itself.

### What the guide does *not* cover

The EWARS migration also touched these things in `scripts/predict.R`, but only because EWARS needed them:

- **Robust `time_period` parsing** — extracting year/month/week from `time_period` strings like `"2023-05"` or `"2023-W22"` when the old pipeline used to pre-expand those columns.
- **Weekly vs monthly dispatch** — branching the model formula based on whether the input data has `week` or `month` columns.
- **Year offset renormalization** — `df$ID_year <- df$ID_year - min(df$ID_year) + 1` to avoid numerical issues in INLA's `rw1` prior on year effects.

If your model does not do these things already, do not add them. They are EWARS's implementation details, not chapkit requirements.

## A.5 Replace the Dockerfile (critical for R models)

> **The scaffolded Dockerfile is Python-only and will not work for R or other non-Python models.** `chapkit init` generates a multi-stage Dockerfile based on `ghcr.io/astral-sh/uv:0.9-python3.13-bookworm-slim`, with a gunicorn + servicekit entry point. There is no R, no INLA, and no path to add them incrementally without rewriting almost every line. For R models, delete the scaffolded `Dockerfile` and replace it with one built on top of an R image, with Python and uv layered on top. A future `chapkit init` template may ship an R-friendly variant; until then this swap is manual.

This repo's `Dockerfile` is the canonical replacement for an R + INLA model (26 lines):

```dockerfile
# R-INLA is amd64-only.
ARG BASE_PLATFORM=linux/amd64

FROM --platform=${BASE_PLATFORM} ghcr.io/mortenoh/r-docker-images/my-r-inla-mini:latest

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON=python3.13 \
    UV_PYTHON_PREFERENCE=only-system \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY main.py ./
COPY scripts/ ./scripts/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Key structural differences from the scaffolded Dockerfile

- **Base image:** an R image (`my-r-inla-mini`) instead of a Python slim image. Your R environment is the foundation; Python is layered on top.
- **uv is copied from a helper image** (`ghcr.io/astral-sh/uv:0.11`) rather than installed from pip, because the R base image may not ship Python tooling.
- **Single stage** instead of builder + runtime split. For model-serving images the separation is not worth the added complexity.
- **Direct `uvicorn` CMD** instead of the scaffold's gunicorn + servicekit config. Simpler to debug, good enough for model workloads, and matches what `uv run uvicorn main:app` does locally.
- **No unprivileged user, no HEALTHCHECK, no tini.** The scaffold adds these as production hardening; for a model-serving container they are optional and can be added back later if your deployment needs them.

### Things to adapt for your specific model

- Swap the `FROM` line for your model's base image. R + INLA use the one above. For a plain R model without INLA, use an `r-base` or `rocker/*` image. For Python scientific stacks, you could keep the scaffolded Dockerfile if it already works.
- If your base image does not include `python3.13`, install it before the `ENV` block. On newer Ubuntu-based images (24.04+) a plain `apt-get install python3.13` may work. On older images (e.g. Ubuntu 22.04 Jammy), Python 3.13 is not in the default repositories — use the [deadsnakes PPA](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa) instead:
  ```dockerfile
  RUN apt-get update && \
      apt-get install -y ca-certificates curl gpg && \
      curl -fsSL 'https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xF23C5A6CF475977595C89F51BA6932366A755776' \
        | gpg --dearmor -o /etc/apt/trusted.gpg.d/deadsnakes.gpg && \
      echo "deb http://ppa.launchpad.net/deadsnakes/ppa/ubuntu jammy main" \
        > /etc/apt/sources.list.d/deadsnakes.list && \
      apt-get update && \
      apt-get install -y python3.13 && \
      rm -rf /var/lib/apt/lists/*
  ```
  Replace `jammy` with your base image's Ubuntu codename. This approach is preferred over `uv python install` because it guarantees the correct architecture — important when building amd64 images on Apple Silicon hosts.
- Pin `--platform=linux/amd64` only if one of your native dependencies (INLA, TMB, PROJ/GDAL builds, ...) is amd64-only. When you do, also add `platform: linux/amd64` to the service in `compose.yml` — without it, Docker Compose may warn or refuse to run the image on ARM hosts (Apple Silicon Macs). **Apple Silicon users:** you also need Rosetta installed (`softwareupdate --install-rosetta`) and enabled in Docker Desktop (Settings → General → "Use Rosetta for x86\_64/amd64 emulation on Apple Silicon"). This is easy to miss if you are coming from a Windows or Intel Mac environment where amd64 images run natively.
- Copy any extra asset directories your scripts read from (`example_data/`, `shapefiles/`, ...).

The `uv.lock`-driven install (`uv sync --frozen --no-dev --no-install-project`) is the recommended pattern — it is fast and reproducible.

## A.6 Move your model code into the scaffold

You have two ergonomic choices for combining the scaffold with your existing repo:

1. **Treat the scaffold as the new repo.** Copy your R scripts, example data, and any other assets into the scaffolded project and commit from there. Push to a new repo or replace the old one.
2. **Copy the scaffold into your existing repo.** Cherry-pick `main.py`, `pyproject.toml`, `Dockerfile`, `compose.yml`, and `scripts/` out of the scaffold and paste them into your existing repo alongside your R scripts. Delete the placeholder scripts.

Either way, remove every file that existed only to run the old non-chapkit pipeline:

- `MLproject` (MLflow manifest).
- Flat YAML configs (`example_config.yaml`, `pure_config.yaml`) — the Pydantic class owns the schema now.
- Custom orchestration wrappers (`isolated_run.R`, bash runners, `run.py`, ...).
- Pre-computed prediction CSVs and model binaries committed under `example_data/` — they bloat the repo and go stale.
- Old root-level `train.R` / `predict.R` that were replaced by the files in `scripts/`.

Keep: modeling code, helper libraries (`scripts/lib.R`), example data inputs, and anything referenced by your scripts at runtime.

## A.7 Verify the migration

This is the gate — if `chapkit test` passes, your required migration is done.

1. Sync Python deps:

   ```
   uv sync
   ```

2. Start the service locally:

   ```
   uv run uvicorn main:app --host 0.0.0.0 --port 8000
   ```

3. In another shell, hit `/health`:

   ```
   curl http://localhost:8000/health
   ```

   Expect `{"status":"healthy", ...}`.

4. Inspect `/api/v1/info` and confirm it reflects your `MLServiceInfo`:

   ```
   curl http://localhost:8000/api/v1/info
   ```

5. Run the built-in end-to-end test. Because you are now inside a chapkit project, the `chapkit` CLI exposes the `test` subcommand:

   ```
   chapkit test --url http://localhost:8000 --verbose
   ```

   This creates a config, submits one training job, submits one prediction job, and polls for completion. On slow models bump `--timeout` (default 60s, often too tight for R-INLA). See this repo's `ci.yml` for the `--timeout 180` value used in CI.

6. Build the container and rerun step 5 against it:

   ```
   docker compose build
   docker compose up -d
   # Wait for the service to be healthy before running tests — may take
   # 10-30s on slower machines or under emulation (Apple Silicon + amd64).
   until curl -sf http://localhost:8000/health > /dev/null 2>&1; do sleep 2; done
   chapkit test --url http://localhost:8000 --timeout 180 --verbose
   ```

When `chapkit test` reports `ALL TESTS PASSED` both locally and against the container, Part A is complete.

### What to expect before R is installed

If you run steps 1-5 on a host that does not have R and the required R packages (common on macOS developer machines), you will see:

- `/health` returns healthy.
- `/api/v1/info` reflects your `MLServiceInfo`.
- `chapkit test` creates the config, submits the training job, and then reports a prediction failure with `Training script exited with code 127`. Exit 127 is the shell's "command not found" — chapkit tried to run `Rscript` and the host has no R.

That failure mode is expected on non-R hosts and is not a guide bug. The actual R execution has to happen inside the Docker container (step 6), where the R base image provides `Rscript`. This sequence was verified end-to-end while writing this guide — a scaffold of `ewars_template` via `chapkit init` plus the Part A edits boots cleanly, serves `/health` and `/info`, and drives `chapkit test` up to the `Rscript` invocation.

---

# Part B — Optional

Everything below is polish. Add whatever pays off for your workflow. The current repo has all of these as reference implementations.

## B.1 Makefile

A `Makefile` saves contributors from memorizing `docker build` flags and `uv run` invocations. See `Makefile` for a ~30-line template with `build`, `run`, `run-ghcr`, and `lint` targets. `make build` in this repo uses `--no-cache` so CI-equivalent images are reproducible; drop that flag if iteration speed matters more than freshness.

## B.2 Ruff lint and format

Add a `[tool.ruff]` section to `pyproject.toml` and wire it into a `make lint` target. Useful because `main.py` is often the only Python file in a chapkit repo — without a linter, small drift accumulates fast. See `pyproject.toml` for the config used here.

## B.3 Docker Compose

Two small compose files make local development painless:

- `compose.yml` — builds the image locally and runs it with a health check.
- `compose.ghcr.yml` — pulls the prebuilt image from GHCR instead of building.

The scaffold already ships `compose.yml`. Add `compose.ghcr.yml` once you start publishing images.

## B.4 GitHub Actions CI

`.github/workflows/ci.yml` in this repo runs two jobs on push and pull request to `main`:

- **lint** — installs uv and Python 3.13, runs `make lint`.
- **docker-test** — builds the image via buildx with `load: true`, starts the container, polls `/health` until healthy, installs chapkit, runs `chapkit test --url http://localhost:8000 --timeout 180 --verbose`, dumps container logs on failure, and tears down the container on always.

The `docker-test` job is the one that matters most — it is an actual functional test of your migrated service running inside the container, not just a "did the build succeed" check. Copy it wholesale.

## B.5 GHCR publish workflow

`.github/workflows/publish-docker.yml` builds and pushes the image to `ghcr.io/<org>/<repo>` on pushes to `main` and on version tags. It uses `docker/metadata-action` to tag with `latest`, the short SHA, branch names, and semver.

## B.6 Monitoring stack

`chapkit init --with-monitoring` generates a Prometheus + Grafana stack alongside the compose file. Use it when you want to observe request latency, job throughput, and service health locally or in staging. Can also be added manually later by copying the `monitoring/` directory from a freshly scaffolded project.

## B.7 Example data

Commit a small `example_data/` directory with canonical CHAP-format CSVs so contributors (and `chapkit test` locally) have something to run against. Keep file sizes modest. This repo has `example_data/` and `example_data_monthly/` with weekly and monthly variants.

## B.8 CHAP Core self-registration (important for live deployments)

If you are going to run this service against a real CHAP Core instance, the service should register itself on startup so CHAP Core knows about it. Without registration the service still works over HTTP — you just have to point CHAP Core at it by hand. With registration, CHAP Core picks it up automatically and keeps track of its health.

Add `.with_registration(keepalive_interval=15)` to the `MLServiceBuilder` chain (already shown in section A.3.5). Registration is controlled entirely by environment variables — when `SERVICEKIT_ORCHESTRATOR_URL` is set, the service registers on startup; when unset, registration is skipped and the service runs standalone.

What you get:

- **Automatic registration after app startup**, with retries on failure (default: 5 retries, 2s apart). Registration fires after all routes are mounted, so chap-core's inline sync can immediately fetch configs from the service.
- **Hostname auto-detection** — works inside a Docker container where the hostname is the container name.
- **Keepalive pings** — the service re-announces itself every 15s with a 30s TTL, so CHAP Core can tell when it goes away.
- **Auto re-registration on 404** — if chap-core's Redis registry loses the service entry (e.g. after a chap restart), the keepalive loop detects the 404 and falls back to a fresh `register_service()` call automatically.
- **Graceful deregistration on shutdown** — the service tells CHAP Core it is going offline instead of just disappearing.

Environment variables:

- `SERVICEKIT_ORCHESTRATOR_URL` — the registration endpoint URL (e.g. `http://chap:8000/v2/services/$register`). When unset, registration is skipped entirely.
- `SERVICEKIT_REGISTRATION_KEY` — the shared secret (only if CHAP Core has registration keys enabled).

This is in Part B because the service runs fine without it for local development, CI, and `chapkit test`. But if the destination is a live chap-core environment, enabling it is effectively required — without it, CHAP Core will not know your service exists until an operator registers it manually.

## B.9 Documentation

- `README.md` — overview, quickstart, how to run `chapkit test` locally, link to this guide. The scaffold generates a starter README.
- `CLAUDE.md` or equivalent contributor docs — project conventions (commit style, branch naming, code-style rules).

---

# Migration checklist

Required — do not ship without these:

- [ ] `chapkit` CLI installed globally (`uv tool install chapkit`)
- [ ] Project scaffolded with `chapkit init <name> --template ml-shell` from *outside* any existing chapkit project
- [ ] `main.py` config class, command templates, and `MLServiceInfo` customized for your model
- [ ] Placeholder scripts in `scripts/` replaced with your real train and predict commands (any language)
- [ ] Scripts parse named CLI flags matching your command templates
- [ ] Scripts read `config.yml` from the working directory
- [ ] Column adapter applied in the predict script (CHAP canonical → internal names)
- [ ] `source()` paths updated if scripts moved from repo root to `scripts/`
- [ ] **For R / non-Python models:** scaffolded `Dockerfile` fully replaced with one based on your model's base image (e.g. R + INLA) with Python and uv layered on top
- [ ] Old MLproject / YAML configs / orchestration files removed
- [ ] `chapkit test` passes against the built container (the definitive test — verifies R, Python, and chapkit all work together)
- [ ] `chapkit test` passes locally (`uvicorn` on host) — optional; only meaningful on hosts that have the modeling runtime installed (e.g. R + INLA)

Optional — add as useful:

- [ ] Makefile
- [ ] Ruff lint and format
- [ ] `compose.yml` (shipped by scaffold) and `compose.ghcr.yml`
- [ ] CI workflow (lint + docker-test)
- [ ] GHCR publish workflow
- [ ] Monitoring stack (`--with-monitoring`)
- [ ] Example data committed
- [ ] README and contributor docs

---

# Reference: files in this repo

The canonical worked example for every section above.

| File | What to look at |
|---|---|
| `main.py` | Full chapkit service definition — ~100 lines, imports through `.build()` |
| `pyproject.toml` | Minimal Python manifest with chapkit dep |
| `scripts/train.R` | Minimal named-arg parser and no-op training pattern |
| `scripts/predict.R` | `apply_adapters()`, `parse_config()`, and model invocation |
| `Dockerfile` | Python 3.13 and uv on top of R-INLA base image |
| `Makefile` | `build`, `run`, `run-ghcr`, `lint` targets |
| `compose.yml`, `compose.ghcr.yml` | Local and remote run recipes |
| `.github/workflows/ci.yml` | Lint job plus docker-test job running `chapkit test` against the container |
| `.github/workflows/publish-docker.yml` | GHCR publish on push to main and version tags |

---

# Appendix: manual scaffolding (when you cannot use `chapkit init`)

If for any reason you cannot run `chapkit init` (e.g. you are patching an existing repo in place and moving to a parent directory is inconvenient), you can write the same files by hand. The scaffold is not magic — it is a small set of templates. This section lists the minimum files you need.

**`pyproject.toml`**

```toml
[project]
name = "your-model-template"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["chapkit>=0.17.1"]
```

Then run `uv sync` to generate `uv.lock`.

**`main.py`**

Start from the imports, config class, runner, service info, hierarchy, and builder shown in sections A.3.1 through A.3.5 above. The 77-line `main.py` in this repo is a complete working reference — copy it and edit the fields.

**`Dockerfile`**

Copy the Dockerfile shown in section A.5.

**`scripts/train.R` and `scripts/predict.R`**

Write your model scripts following the contract in section A.4: named CLI flags, `config.yml` parsing, column adapter, output to `{output_file}`.

Everything from A.6 onward (merge, cleanup, verify) applies identically.

---

# Out of scope

This guide deliberately does not cover:

- Writing R or Python modeling code from scratch.
- Choosing a base image — it depends on your model's native dependencies.
- CHAP platform registration or deployment beyond building a container.
- Multi-model repositories — the template assumes one model per repo.
