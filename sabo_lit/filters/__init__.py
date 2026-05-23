"""
filters/ — secondary ICT/SMC filters.

Each filter implements ``SecondaryFilter`` and returns a ``FilterResult``
with a bounded ``score_adjustment``. Filters CANNOT create or revoke a
``ValidatedStructureState``; their only effect is to nudge a confidence
score on a setup the gate has already validated.
"""
