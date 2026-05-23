"""
tests/ — mirrors the production tree.

Every public interface in ``core.interfaces`` has at least one contract
test (a typed fake plus a behavioural assertion). The governance suite
includes a test that runs ``DependencyPolicy.scan`` and fails on any
``DependencyViolation`` — research-to-production imports break CI.
"""
