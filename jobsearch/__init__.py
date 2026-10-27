"""uk-job-search: a UK job-search framework over official job-board APIs.

Deterministic by design: searching, filtering and scoring use plain Python, and
the only place a language model is involved is the optional prompt pack in
``prompts/`` that a human (or an agent the human invokes) applies deliberately.

Not affiliated with Adzuna or Reed.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
