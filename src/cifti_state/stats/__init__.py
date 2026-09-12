"""Group statistics on the surface: the model, and what survives correction.

Everything in this sub-package answers one of two questions.

**What is the effect?**  A vertex-wise linear model across subjects, and one
contrast tested through it -- a one-sample, two-sample or paired t-test, with
covariates if the design needs them.

**Which of it is real?**  Two family-wise error corrections over the whole
surface: random field theory, which reads the answer off the smoothness of the
model's residuals, and permutation, which builds the null distribution by
rearranging the data.  They rest on different assumptions, so running both and
comparing them costs one extra argument and buys a great deal of confidence.

A worked run::

    from cifti_state import load_settings
    from cifti_state.stats import load_participants, two_sample_t

    settings = load_settings()
    people = load_participants("study/participants.csv")
    analysis = two_sample_t(
        people, settings, "group",
        covariates=["age", "sex"],
        permutations=5000, cluster_forming=3.1,
    )
    print(analysis.summary())

``analysis.stat_map`` is an ordinary
:class:`~cifti_state.io.cifti.SurfaceStatMap`, so the clustering, reporting and
figures already in the package take it directly; ``analysis.correct_clusters``
then attaches corrected P values to whatever clusters you formed from it.
"""

from .data import SurfaceDataset, load_dataset
from .design import (
    Design,
    DesignError,
    Participants,
    load_participants,
    one_sample_design,
    paired_design,
    two_sample_design,
)
from .glm import GLMFit, fit_glm, t_to_z, welch_two_sample
from .permutation import PermutationResult, permutation_test
from .resels import ReselEstimate, SurfaceTopology, build_topology, compute_resels, edge_roughness
from .rft import RandomField, ec_density
from .tfce import PALM_2D, PALM_DEFAULT, TFCEEngine, TFCESettings, tfce
from .ttest import (
    CORRECTION_COLUMNS,
    GroupAnalysis,
    one_sample_t,
    paired_t,
    run_test,
    two_sample_t,
)

__all__ = [
    # study description
    "Participants",
    "load_participants",
    "Design",
    "DesignError",
    "one_sample_design",
    "two_sample_design",
    "paired_design",
    # data
    "SurfaceDataset",
    "load_dataset",
    # model
    "GLMFit",
    "fit_glm",
    "welch_two_sample",
    "t_to_z",
    # smoothness and RFT
    "SurfaceTopology",
    "build_topology",
    "edge_roughness",
    "ReselEstimate",
    "compute_resels",
    "RandomField",
    "ec_density",
    # permutation
    "PermutationResult",
    "permutation_test",
    # threshold-free cluster enhancement
    "TFCESettings",
    "TFCEEngine",
    "tfce",
    "PALM_DEFAULT",
    "PALM_2D",
    # the tests
    "GroupAnalysis",
    "run_test",
    "one_sample_t",
    "two_sample_t",
    "paired_t",
    "CORRECTION_COLUMNS",
]
