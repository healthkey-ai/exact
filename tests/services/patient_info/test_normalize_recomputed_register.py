"""The register of recomputed attributes must match what the module writes.

`RECOMPUTED_ATTRIBUTES` exists so a client can tell which values EXACT will
replace whatever is stored upstream (#449). A list that drifts from the code is
worse than no list: it would tell an editing client that a field is safe to
offer when the next match will overwrite it. So the two are compared here
rather than trusted to stay in step.

The comparison is over the module's syntax, not its behaviour: every `pi.x = `
in `normalize.py` is an attribute the normaliser decides.
"""
import ast
import inspect

from trials.services.patient_info import normalize


def _assigned_attributes():
    tree = ast.parse(inspect.getsource(normalize))
    found = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == 'pi'):
                found.add(target.attr)
    return found


def test_register_lists_every_attribute_the_module_assigns():
    assigned = _assigned_attributes()
    missing = assigned - normalize.RECOMPUTED_ATTRIBUTES
    assert not missing, (
        'normalize.py computes these but RECOMPUTED_ATTRIBUTES does not list '
        f'them, so a client would offer an edit box that the next match '
        f'silently undoes: {sorted(missing)}'
    )


def test_register_claims_nothing_the_module_does_not_compute():
    assigned = _assigned_attributes()
    extra = normalize.RECOMPUTED_ATTRIBUTES - assigned
    assert not extra, (
        'RECOMPUTED_ATTRIBUTES names these but normalize.py no longer computes '
        f'them, so a client is withholding an edit it could offer: {sorted(extra)}'
    )


def test_the_register_is_not_empty():
    # A regex or AST walk that silently matched nothing would make both tests
    # above pass while proving nothing at all.
    assert len(normalize.RECOMPUTED_ATTRIBUTES) > 20
