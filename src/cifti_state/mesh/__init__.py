"""Meshes, and moving data between them.

Three questions, one per module.

**Which surface is this?** :mod:`~cifti_state.mesh.spaces` names every mesh the
package knows as a (family, density) pair -- ``fsLR:32k``, ``fsaverage5`` --
because a vertex count alone is not an answer: fs_LR 10k and fsaverage5 both
have 10242 vertices per hemisphere and are not interchangeable.

**Where are its template files?** :mod:`~cifti_state.mesh.templates` finds the
spheres, area metrics, medial-wall masks and anatomical surfaces by their
standard HCP and FreeSurfer names, so a configuration lists directories rather
than sixty filenames, and says precisely what is missing when something is.

**How does data get from one to another?**
:mod:`~cifti_state.mesh.resample` runs Workbench's ``-metric-resample`` with
the right spheres, the area metrics that make ``ADAP_BARY_AREA`` mean what it
says, the medial wall held out of the interpolation, and a route through an
intermediate mesh when no direct one exists.

A whole conversion is one call::

    from cifti_state import load_settings
    from cifti_state.mesh import resample_cifti

    settings = load_settings()
    result = resample_cifti("sub-01_bold.dtseries.nii", "sub-01_10k.dtseries.nii",
                            "fsLR:10k", settings)
    print(result.describe())

and everything it ran is in ``result.commands``, ready to paste into a shell.
"""

from .project import (
    ReportProjection,
    nearest_target_vertices,
    project_clusters,
    project_for_report,
    project_stat_map,
)
from .resample import (
    DEFAULT_METHOD,
    METHODS,
    ResampleResult,
    ResampleStep,
    plan_route,
    resample_cifti,
    resample_label_gifti,
    resample_metric,
)
from .spaces import (
    KNOWN_MESHES,
    MeshError,
    MeshSpace,
    identify_mesh,
    list_meshes,
    parse_mesh,
)
from .templates import SURFACE_KINDS, MeshFiles, MeshLibrary, common_registration

__all__ = [
    # naming
    "MeshSpace",
    "MeshError",
    "KNOWN_MESHES",
    "parse_mesh",
    "identify_mesh",
    "list_meshes",
    # templates
    "MeshFiles",
    "MeshLibrary",
    "SURFACE_KINDS",
    "common_registration",
    # resampling
    "ResampleStep",
    "ResampleResult",
    "plan_route",
    "resample_metric",
    "resample_label_gifti",
    "resample_cifti",
    "METHODS",
    "DEFAULT_METHOD",
    # projecting a finished analysis onto the report mesh
    "ReportProjection",
    "project_for_report",
    "project_stat_map",
    "project_clusters",
    "nearest_target_vertices",
]
