"""Pure-Python uncertainty-aware adaptive smart film control and stabilizer for FR-04.

Implements multi-objective Dynamic Programming (Viterbi) horizon optimization,
gap filling (FR-04.1), minimum holding time (FR-04.2), dynamic hysteresis (FR-04.3),
cell-edge margins (FR-04.4), policy hyperparameter tuning, and LG SGF continuous dimming.
"""

from .dp_controller import (
    AdaptivePolicyTuner,
    apply_cell_edge_margin,
    apply_fr04_stabilization,
    build_shading_plan_from_trajectories,
    compare_baseline_vs_adaptive,
    compute_stage_risk,
    get_candidate_levels,
    optimize_cell_dp,
)
from .models import (
    AdaptiveComparisonMetrics,
    AdaptiveWeights,
    CellObservation,
    CellState,
    CellTrajectory,
    HardwareFilmMode,
    OpticalProfileData,
    OptimizedCellStep,
    ProtectionGrade,
    UncertaintyQuantiles,
)

__all__ = [
    "AdaptiveComparisonMetrics",
    "AdaptivePolicyTuner",
    "AdaptiveWeights",
    "CellObservation",
    "CellState",
    "CellTrajectory",
    "HardwareFilmMode",
    "OpticalProfileData",
    "OptimizedCellStep",
    "ProtectionGrade",
    "UncertaintyQuantiles",
    "apply_cell_edge_margin",
    "apply_fr04_stabilization",
    "build_shading_plan_from_trajectories",
    "compare_baseline_vs_adaptive",
    "compute_stage_risk",
    "get_candidate_levels",
    "optimize_cell_dp",
]
