"""Step executors (spec 005): llm, llm_tools, tool, code, notify, subflow, wait, guard; spec 008 adds fetch,
sanitize and reader; spec 009 adds output_guard and the security-flag code functions;
spec 010 adds session_plan; spec 011 adds the context refresh code functions.

Importing this package registers the built-in step types in :data:`STEPS` and the built-in ``code``
functions in :data:`CODE`. Later specs add a step type by defining a :class:`StepExecutor` subclass with
``@register_step`` in their own module and listing that module in :data:`BUILTIN_MODULES` (or, outside this
package, under the ``pkpipe.steps`` entry point group). The engine itself never changes.
"""

from importlib import import_module

from pkpipe.steps.base import (
    CODE,
    CODE_DATA_CLASS,
    STEPS,
    NoConfig,
    RunInfo,
    StepConfig,
    StepContext,
    StepError,
    StepExecutor,
    StepPorts,
    StepRegistry,
    StepResult,
    datamark,
    datamark_notice,
    datamark_token,
    load_plugins,
    register_code,
    register_step,
)

#: Modules whose import registers step types / code functions.
BUILTIN_MODULES = (
    "pkpipe.steps.tool",
    "pkpipe.steps.llm",
    "pkpipe.steps.flow_control",
    "pkpipe.steps.triage_code",
    "pkpipe.steps.report_code",
    "pkpipe.steps.discovery_code",
    "pkpipe.steps.improvement_code",
    "pkpipe.steps.secure_fetch",
    "pkpipe.steps.learning_code",
    "pkpipe.steps.output_guard",
    "pkpipe.steps.security_code",
    "pkpipe.steps.session_plan",
    "pkpipe.steps.context_code",
    "pkpipe.steps.image_code",
)

for _module in BUILTIN_MODULES:
    import_module(_module)

__all__ = [
    "BUILTIN_MODULES",
    "CODE",
    "CODE_DATA_CLASS",
    "STEPS",
    "NoConfig",
    "RunInfo",
    "StepConfig",
    "StepContext",
    "StepError",
    "StepExecutor",
    "StepPorts",
    "StepRegistry",
    "StepResult",
    "datamark",
    "datamark_notice",
    "datamark_token",
    "load_plugins",
    "register_code",
    "register_step",
]
