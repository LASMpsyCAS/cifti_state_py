"""cifti_state -- fsLR surface cluster analysis, reporting and visualisation.

A Python rewrite of the MATLAB pipeline in ``Cifti_state``: threshold a
surface statistic map, label connected clusters, measure their peaks, name them
against an atlas, tabulate the result and draw it.

Quick start::

    from cifti_state import load_settings, AnalysisSpec, run_analysis

    settings = load_settings()
    spec = AnalysisSpec(
        input_path="group_mean_thresh_fdr_E_C.dscalar.nii",
        threshold_method="fixed", threshold_value=1.039,
        extent=20, atlas="Glasser_2016",
        output_dir="results",
    )
    result = run_analysis(spec, settings)
    print(result.summary())
    print(result.report.head())

The layers are deliberately separate:

``cifti_state.io``
    reads and writes files
``cifti_state.core``
    pure functions over arrays -- no IO, no config, no printing
``cifti_state.viz``
    surfplot rendering into matplotlib figures
``cifti_state.pipeline``
    the orchestration the CLI and the GUI share
"""

from .config import Settings, load_settings
from .logging_setup import configure_logging, get_logger
from .pipeline import run_analysis
from .results import AnalysisResult, AnalysisSpec
from .types import OperationCancelled

__version__ = "0.1.0"

__all__ = [
    "AnalysisResult",
    "AnalysisSpec",
    "OperationCancelled",
    "Settings",
    "__version__",
    "configure_logging",
    "get_logger",
    "load_settings",
    "run_analysis",
]
