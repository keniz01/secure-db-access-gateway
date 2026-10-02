"""Named policy-evaluator stubs for tests.

``GovernedQueryGateway`` no longer defaults its policy evaluator, because the old
default (``PolicyEvaluator(enabled=False)``) silently ALLOWED every request.
Tests that are not exercising authorization must now opt in to a permissive
evaluator explicitly, by importing a name from this module.

That is the point: a policy bypass in the test suite is now greppable
(``grep -rn permissive_policy_evaluator tests/``) and each use site documents
itself, instead of being an invisible consequence of omitting an argument.

Prefer one of:

* :func:`permissive_policy_evaluator` - ALLOW-ALL. For tests whose subject is
  quota accounting, SQL safety, audit shape, or error handling, where the
  authorization decision is incidental.
* :func:`deny_all_policy_evaluator` - DENY-ALL. For tests that assert a
  request is rejected, or that must prove no bypass is possible.
* A real ``PolicyEvaluator([...], enabled=True)`` with explicit policies, for
  anything that asserts authorization behaviour. See
  ``tests/security/test_authorization_pipeline_fuzz.py``.

A hand-rolled evaluator class should be a last resort: if a test needs one,
prefer expressing the decision as a real policy document so the test exercises
the production evaluation path.
"""

from services.policy_engine import PolicyEvaluator

__all__ = [
    "deny_all_policy_evaluator",
    "permissive_policy_evaluator",
]


def permissive_policy_evaluator() -> PolicyEvaluator:
    """An ALLOW-ALL evaluator. Only for tests that are not testing authorization."""
    return PolicyEvaluator(enabled=False)


def deny_all_policy_evaluator() -> PolicyEvaluator:
    """A DENY-ALL evaluator: enabled, but with no policy that matches anything.

    Fails closed, mirroring the production posture when a policy document is
    present but grants nothing.
    """
    return PolicyEvaluator((), enabled=True)
