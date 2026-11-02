"""System preflight: probe the host and the LLM server, decide how to drive it, trace it, keep sampling.

``run_preflight`` (one snapshot + decision + ``system.preflight`` span + gauges), ``SystemSampler`` (a
background thread re-sampling during a long run), ``pkpipe doctor`` (``pkpipe.system.cli``). The policy is
:func:`pkpipe.domain.system.decide`; the probe adapters are in :mod:`pkpipe.adapters.system_probe`.
"""

from pkpipe.system.preflight import (
    Preflight,
    gauge_values,
    model_targets,
    run_preflight,
    span_attributes,
)
from pkpipe.system.sampler import SamplerSummary, SystemSampler

__all__ = [
    "Preflight",
    "SamplerSummary",
    "SystemSampler",
    "gauge_values",
    "model_targets",
    "run_preflight",
    "span_attributes",
]
