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


def _patient_holders(tree):
    """Which local name holds the patient, in each function — `{fn: {names}}`.

    Followed from the entry point rather than guessed from shape. An earlier
    version took every function's FIRST parameter, which is the same answer
    for this module today and a trap tomorrow: an unrelated helper taking some
    other object would have had its writes counted as patient writes, and the
    register would be failed for a field nothing on the patient ever gets.

    So: start at `normalize_patient_info`'s own parameter, and carry it to any
    module-level function this module CALLS with a patient as its first
    argument. Aliases (`alias = pi`) are tracked per function, because a name
    bound in one function says nothing about the same name in another.

    The hole this exists to close is real: the sibling module
    `patient_info_attributes.py` spells the same object `self.patient_info`,
    so a normaliser copied from there and renamed wrote invisibly, and the
    register went on telling a client the value was theirs.
    """
    funcs = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def first_arg(fn):
        args = fn.args.posonlyargs + fn.args.args
        return args[0].arg if args else None

    entry = funcs.get('normalize_patient_info')
    assert entry is not None, 'normalize.py no longer has normalize_patient_info'
    holders = {'normalize_patient_info': {first_arg(entry)}}

    # Anything this module never calls itself is part of its outward surface,
    # and what the caller passes cannot be seen from here. Seeded as a holder
    # rather than ignored: a normaliser wired in from `resolve.py` — which is
    # exactly where the next one would go, beside the existing
    # `normalize_patient_info(pi)` call — was invisible to the previous
    # version of this walk, and the register went on claiming to be complete.
    #
    # This is the direction to be wrong in. A function that is NOT the patient
    # and is never called here produces a loud failure demanding a name be
    # registered, which someone reads; the other way round the register lies
    # quietly for ever.
    called_here = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    for name, fn in funcs.items():
        if name not in called_here and first_arg(fn):
            holders.setdefault(name, set()).add(first_arg(fn))

    changed = True
    while changed:
        changed = False
        for name, fn in funcs.items():
            names = set(holders.get(name, ()))
            if not names:
                continue
            for node in ast.walk(fn):
                # `alias = pi`, within this function only.
                if (isinstance(node, ast.Assign)
                        and isinstance(node.value, ast.Name)
                        and node.value.id in names):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id not in names:
                            names.add(target.id)
                            changed = True
                # `_normalize_x(pi)` — the callee's parameter holds it too.
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in funcs):
                    callee_arg = first_arg(funcs[node.func.id])
                    passed = (
                        node.args
                        and isinstance(node.args[0], ast.Name)
                        and node.args[0].id in names
                    ) or any(
                        # `_normalise(pi=pi)` — a keyword call has no
                        # positional args at all, so matching only those made
                        # the callee invisible.
                        kw.arg == callee_arg
                        and isinstance(kw.value, ast.Name)
                        and kw.value.id in names
                        for kw in node.keywords
                    )
                    if passed and callee_arg:
                        target = holders.setdefault(node.func.id, set())
                        if callee_arg not in target:
                            target.add(callee_arg)
                            changed = True
            holders[name] = names
    return funcs, holders


def _setattr_is_the_builtin(tree):
    """That nothing in this module binds the name `setattr`.

    The walk below reads `setattr(pi, 'x', v)` as a write. Anything that
    rebinds the name makes it something else, and the walk would be describing
    a function nobody called.

    Every binding form, not just assignment and import: a parameter named
    `setattr`, a `for` target, a `with ... as`, a walrus, an `except ... as`.
    Absurd code, all of it — which is the point. A guard that only looks where
    it expects the problem is not a guard.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == 'setattr':
                return False
            arguments = node.args
            named = (
                arguments.posonlyargs + arguments.args + arguments.kwonlyargs
                + [a for a in (arguments.vararg, arguments.kwarg) if a]
            )
            if any(argument.arg == 'setattr' for argument in named):
                return False
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any((a.asname or a.name.split('.')[0]) == 'setattr' for a in node.names):
                return False
        if isinstance(node, ast.ExceptHandler) and node.name == 'setattr':
            return False
        targets = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
            targets = [node.target]
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets = [node.target]
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            targets = [item.optional_vars for item in node.items if item.optional_vars]
        while targets:
            target = targets.pop()
            if isinstance(target, (ast.Tuple, ast.List)):
                targets.extend(target.elts)
            elif isinstance(target, ast.Name) and target.id == 'setattr':
                return False
    return True


# What this walk is and is not
# ----------------------------
# A guard over one 250-line module of free functions, not a static analyser.
# It is name-based and flow-insensitive, and three limits follow from that:
#
#   * `called_here` matches by name, so a local or nested function sharing a
#     module function's name would suppress that function's outward seeding;
#   * `ast.walk` descends into nested functions, so a nested parameter sharing
#     a holder's name would be read as the patient — which is also what makes
#     a closure writing `pi.x` visible, and that is worth more here;
#   * aliases ignore rebinding and order, so `alias = pi` followed by
#     `alias = config` leaves `alias` a holder.
#
# None of the three occurs in `normalize.py`, and all three fail toward a LOUD
# complaint about a field that should be registered rather than toward a
# register that lies. That is the direction chosen throughout this file; the
# limits are written down so the next reader does not assume more than it
# gives. `codex review` enumerated them.


def _assigned_attributes():
    tree = ast.parse(inspect.getsource(normalize))
    funcs, holders = _patient_holders(tree)
    found = set()

    for name, fn in funcs.items():
        names = holders.get(name) or set()
        if not names:
            continue
        for node in ast.walk(fn):
            targets = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            while targets:
                target = targets.pop()
                if isinstance(target, (ast.Tuple, ast.List)):
                    targets.extend(target.elts)
                elif (isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id in names):
                    found.add(target.attr)
            # `setattr(pi, 'x', v)` is an assignment the loop above cannot see.
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == 'setattr'
                    and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id in names
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                found.add(node.args[1].value)
    return found


def _dynamic_writes(tree):
    """`setattr` with a name computed at runtime. Unknowable by reading, so
    the register cannot be checked against it at all."""
    funcs, holders = _patient_holders(tree)
    out = []
    for name, fn in funcs.items():
        names = holders.get(name) or set()
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == 'setattr'
                    and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id in names
                    and not (isinstance(node.args[1], ast.Constant)
                             and isinstance(node.args[1].value, str))):
                out.append(node)
    return out


def test_setattr_means_the_builtin():
    assert _setattr_is_the_builtin(ast.parse(inspect.getsource(normalize)))


def test_no_write_hides_behind_a_computed_name():
    """Refused rather than tolerated: a `setattr(pi, some_var, value)` would
    make every check in this file quietly incomplete."""
    assert not _dynamic_writes(ast.parse(inspect.getsource(normalize))), (
        'normalize.py writes a field whose name is computed at runtime; the '
        'register cannot be verified against it. Spell it literally, or the '
        'guard in this file is decoration.'
    )


def test_the_walker_finds_something():
    """Non-vacuity: an empty result would make both directions below agree
    about nothing."""
    assert len(_assigned_attributes()) > 30


def test_an_unrelated_helper_is_not_read_as_the_patient():
    """The false-positive direction, which the first version of this walker
    had: it took EVERY function's first parameter, so a helper taking some
    other object would have had its writes demanded of the register."""
    tree = ast.parse(
        'def normalize_patient_info(pi):\n'
        '    pi.real = 1\n'
        '    _helper(pi)\n'
        '    _unrelated({})\n'
        '\n'
        'def _helper(patient_info):\n'
        '    patient_info.also_real = 2\n'
        '\n'
        'def _unrelated(config):\n'
        '    config.not_a_patient_field = 3\n'
    )
    funcs, holders = _patient_holders(tree)
    assert holders.get('_helper') == {'patient_info'}
    assert not holders.get('_unrelated')


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


# ---------------------------------------------------------------------------
# The conditions, and the values that are not stored at all (#449 follow-up)
# ---------------------------------------------------------------------------


def test_every_condition_describes_a_field_the_module_writes():
    """A condition for a field nothing writes would be an answer about
    nothing — and the first direction above cannot catch it, because a
    conditional field IS in the set."""
    extra = set(normalize.RECOMPUTED_WHEN) - normalize.RECOMPUTED_ATTRIBUTES
    assert not extra, sorted(extra)


def test_the_two_halves_partition_the_set():
    """`RECOMPUTED_ALWAYS` is derived, so this is really a guard on the
    conditions: one that named a field twice, or none at all, would leave the
    always-set describing something untrue."""
    assert normalize.RECOMPUTED_ALWAYS | frozenset(normalize.RECOMPUTED_WHEN) == (
        normalize.RECOMPUTED_ATTRIBUTES
    )
    assert not (normalize.RECOMPUTED_ALWAYS & frozenset(normalize.RECOMPUTED_WHEN))


def test_every_condition_says_something():
    blank = [f for f, w in normalize.RECOMPUTED_WHEN.items() if not (w or '').strip()]
    assert not blank, sorted(blank)


def test_an_always_field_says_always():
    assert normalize.recompute_note('tnbc_status') == {'when': 'always'}


def test_a_conditional_field_carries_its_condition():
    note = normalize.recompute_note('mipi_risk')
    assert note['when'] == 'sometimes'
    assert 'mantle cell lymphoma' in note['condition']


def test_a_field_the_module_leaves_alone_gets_no_note():
    assert normalize.recompute_note('hemoglobin_level') is None


def test_an_unknown_name_does_not_raise():
    assert normalize.recompute_note('not_a_field_at_all') is None
    assert normalize.recompute_note(None) is None


def _read_only_properties():
    from trials.services.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING
    from trials.services.patient_info.patient_info import PatientInfo

    return [
        name
        for name in USER_TO_TRIAL_ATTRS_MAPPING
        if isinstance(getattr(PatientInfo, name, None), property)
        and getattr(PatientInfo, name).fset is None
    ]


def test_there_are_read_only_properties_to_talk_about():
    """Non-vacuity for the two below."""
    assert _read_only_properties()


def test_a_value_that_is_not_stored_is_not_the_readers():
    """The defect this follow-up exists for.

    These are mapped attributes that reach an eligibility row, and are
    properties with no column and no setter: recomputed on every read, and
    dropped from an inbound payload before they reach the instance. Being in
    neither the set nor any condition, they answered "not recomputed" — which
    a client reads as "this value is the reader's", over a field an edit
    cannot reach at all.
    """
    for field in _read_only_properties():
        assert normalize.recompute_note(field) == {'when': 'never-stored'}, field


def test_and_they_really_cannot_be_written():
    """The claim behind the answer, not just the answer."""
    import pytest

    from trials.services.patient_info.patient_info import PatientInfo

    instance = PatientInfo()
    for field in _read_only_properties():
        with pytest.raises(AttributeError):
            setattr(instance, field, 'anything')


def test_a_stored_boolean_is_still_the_readers():
    """`meets_gelf` and `meets_lugano` carry `is_computed_value` and are plain
    stored BooleanFields nothing derives — the pair #449 names as the ones a
    naive reading would wrongly hide. They must not be caught by the net
    above."""
    assert normalize.recompute_note('meets_gelf') is None
    assert normalize.recompute_note('meets_lugano') is None


def test_no_condition_carries_an_issue_number():
    """These strings are shown to a patient.

    The `geo_point` condition shipped as "... (a zero coordinate is dropped —
    #470)" in a first draft. The module's whole rationale for prose is that a
    client puts it in front of a reader, and "#470" means nothing to one. The
    defect it referred to belongs in a comment, which is where it is now.
    """
    import re

    leaks = {
        field: condition
        for field, condition in normalize.RECOMPUTED_WHEN.items()
        if re.search(r'#\d+', condition)
    }
    assert not leaks, leaks


def test_no_condition_is_empty():
    blank = [f for f, w in normalize.RECOMPUTED_WHEN.items() if not (w or '').strip()]
    assert not blank, sorted(blank)
