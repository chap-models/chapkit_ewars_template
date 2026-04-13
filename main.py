import os
from pathlib import Path

from chapkit import BaseConfig
from chapkit.api import AssessedStatus, MLServiceBuilder, MLServiceInfo, ModelMetadata, PeriodType
from chapkit.artifact import ArtifactHierarchy
from chapkit.ml import ShellModelRunner
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
    # BaseConfig reserves `additional_continuous_covariates` as a CHAP-interpreted
    # field. scripts/predict.R reads it into `covariate_names` and wires those
    # columns into `generate_lagged_model`. The default here makes EWARS use
    # rainfall + mean_temperature out of the box (matching the legacy model).
    # Deployments that don't have climate data can override per-config via
    # POST /api/v1/configs with additional_continuous_covariates=[] to run
    # the population-only variant without forking this repo.
    additional_continuous_covariates: list[str] = Field(
        default_factory=lambda: ["rainfall", "mean_temperature"],
        description=(
            "Continuous covariates to include as lagged predictors in the INLA model. "
            "Defaults match the legacy CHAP-EWARS model which used rainfall and "
            "mean_temperature. Override via POST /api/v1/configs to run with a "
            "different covariate set."
        ),
    )


runner: ShellModelRunner[EwarsConfig] = ShellModelRunner(
    train_command="Rscript scripts/train.R --data {data_file}",
    predict_command=(
        "Rscript scripts/predict.R --historic {historic_file} --future {future_file} --output {output_file}"
    ),
)

info = MLServiceInfo(
    id="chapkit-ewars-template",
    display_name="CHAP-EWARS Model (chapkit)",
    version="1.0.0",
    description=(
        "Chapkit-based version of the CHAP-EWARS model, runnable alongside the legacy EWARS model. "
        "Modified version of the World Health Organization (WHO) EWARS model. "
        "EWARS is a Bayesian hierarchical model implemented with the INLA library."
    ),
    model_metadata=ModelMetadata(
        author="CHAP team",
        author_assessed_status=AssessedStatus.orange,
        organization="HISP Centre, University of Oslo",
        organization_logo_url="https://landportal.org/sites/default/files/2024-03/university_of_oslo_logo.png",
        contact_email="knut.rand@dhis2.org",
        citation_info=(
            'Climate Health Analytics Platform. 2025. "CHAP-EWARS model". '
            "HISP Centre, University of Oslo. "
            "https://dhis2-chap.github.io/chap-core/external_models/overview_of_supported_models.html"
        ),
    ),
    period_type=PeriodType.monthly,
    allow_free_additional_continuous_covariates=True,
    required_covariates=["population"],
    min_prediction_periods=0,
    max_prediction_periods=100,
)

hierarchy = ArtifactHierarchy(
    name="ewars",
    level_labels={0: "ml_training_workspace", 1: "ml_prediction"},
)

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
