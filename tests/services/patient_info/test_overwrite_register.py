"""The overwrite register cannot drift from the code that does the writing (#449).

`normalize.py` declares which patient values EXACT recomputes, and an editing
client reads that off the row as `uoverwritten` to decide whether a value is
worth offering a control over. A register maintained by hand is worth nothing
the first time a normalizer gains a field — the register would keep saying a
value is the reader's while the next match quietly reverts it, which is the
exact failure #449 exists to remove.

So the register is checked against the module by parsing it. Parsing rather
than calling: a normalizer only writes on the branch its inputs select, so
exercising `normalize_patient_info` would confirm the register for whatever
fixtures happen to be here and miss every branch they do not reach. The AST
sees all of them.

Both directions are checked. A missing entry is the dangerous one — a value
presented as the reader's that EXACT overwrites. A stale entry is the quiet
one: it hides a control the reader could have had, and nothing would ever
surface it.
"""
import ast
import inspect
from pathlib import Path

import pytest

from trials.services.patient_info import normalize
from trials.services.patient_info.normalize import (
    OVERWRITTEN_ALWAYS,
    OVERWRITTEN_FIELDS,
    OVERWRITTEN_WHEN,
    overwrite_note,
)


def _patient_names_in(tree):
    """What the patient instance is called in this module.

    `pi` today, but hardcoding that is the hole: the sibling module
    `patient_info_attributes.py` spells the same object `self.patient_info`,
    so a normalizer copied from there and renamed would write through a name
    this walker did not watch — and the register would go on claiming a value
    is the reader's. Review demonstrated exactly that: a helper taking
    `patient_info` left all thirteen tests green.

    So the name is taken from each function's own first parameter, plus any
    local bound directly to one of those (`alias = pi`).
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args.posonlyargs + node.args.args
            if args:
                names.add(args[0].arg)
    # A second pass: `alias = pi` rebinds the same object under a new name.
    for _ in range(3):  # to a fixed point, cheaply — aliases of aliases
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Name)
                and node.value.id in names
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    names.discard('self')
    return names


def _fields_written_by_the_module():
    """Every write to the patient instance in normalize.py, wherever it is.

    Covers augmented and annotated assignment, tuple targets and `setattr` —
    not because the module uses them today, but because the point of this test
    is to catch the write nobody thought about.
    """
    tree = ast.parse(Path(inspect.getfile(normalize)).read_text(encoding='utf-8'))
    names = _patient_names_in(tree)
    written = set()

    def record(target):
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id in names
        ):
            written.add(target.attr)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, (ast.Tuple, ast.List)):
                    for element in target.elts:
                        record(element)
                else:
                    record(target)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            record(node.target)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == 'setattr'
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in names
        ):
            # A literal name is recorded; a computed one is not knowable
            # statically, and `test_no_dynamic_writes` refuses it outright.
            if isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                written.add(node.args[1].value)
    return written


def _has_dynamic_write(tree, names):
    """A `setattr` whose field name is not a literal. Unknowable statically,
    so the register cannot be checked against it at all."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == 'setattr'
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in names
            and not (
                isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            )
        ):
            return True
    return False


class TestTheRegisterMatchesTheCode:
    def test_every_field_the_module_writes_is_registered(self):
        missing = _fields_written_by_the_module() - OVERWRITTEN_FIELDS
        assert not missing, (
            f'normalize.py writes {sorted(missing)} but the register does not '
            'list them. A client would offer an edit control over a value the '
            'next match overwrites. Add each to OVERWRITTEN_ALWAYS, or to '
            'OVERWRITTEN_WHEN with the condition named.'
        )

    def test_every_registered_field_is_actually_written(self):
        stale = OVERWRITTEN_FIELDS - _fields_written_by_the_module()
        assert not stale, (
            f'the register lists {sorted(stale)} but normalize.py no longer '
            'writes them, so a control the reader could have had is hidden for '
            'no reason. Remove them.'
        )

    def test_the_two_halves_do_not_overlap(self):
        """`overwrite_note` checks ALWAYS first, so an entry in both would have
        its condition silently ignored — the more permissive answer losing to
        the stricter one without anything saying so."""
        assert not (OVERWRITTEN_ALWAYS & frozenset(OVERWRITTEN_WHEN))

    def test_no_write_is_hidden_behind_a_computed_name(self):
        """`setattr(pi, some_variable, value)` cannot be checked against the
        register by reading, so the guard would go quiet without saying so.
        Refused rather than tolerated."""
        tree = ast.parse(Path(inspect.getfile(normalize)).read_text(encoding='utf-8'))
        assert not _has_dynamic_write(tree, _patient_names_in(tree)), (
            'normalize.py writes a field whose name is computed at runtime; '
            'the register cannot be verified against it. Write it literally, '
            'or the guard here is decoration.'
        )

    def test_this_test_can_fail(self):
        """Non-vacuity. `_fields_written_by_the_module` returning an empty set
        — a renamed parameter, a parse that silently found nothing — would make
        both directions above pass by agreeing about nothing."""
        assert len(_fields_written_by_the_module()) > 30


class TestTheRegisterIsAboutRealFields:
    def test_every_registered_name_is_a_patient_attribute(self):
        """A typo in the register is invisible otherwise: the AST check above
        compares it against the same typo in the module.

        Against an INSTANCE, not `_meta.get_fields()`. Not every value
        `normalize.py` writes is a column: `geo_point` is assigned in
        `__init__` and deliberately kept out of `_FIELDS` because it is
        PostGIS-only. Checking the model's columns would reject a field the
        module really does write — and the register really does need to carry,
        since a client asking about it deserves the same answer.
        """
        from trials.services.patient_info.patient_info import PatientInfo

        instance = PatientInfo()
        unknown = {field for field in OVERWRITTEN_FIELDS if not hasattr(instance, field)}
        assert not unknown, f'not PatientInfo attributes: {sorted(unknown)}'

    def test_that_check_can_fail(self):
        """Non-vacuity: `hasattr` on a Django model is broad, so confirm it
        still says no to something."""
        from trials.services.patient_info.patient_info import PatientInfo

        assert not hasattr(PatientInfo(), 'not_a_field_at_all')

    def test_every_condition_says_something(self):
        blank = [field for field, when in OVERWRITTEN_WHEN.items() if not (when or '').strip()]
        assert not blank, f'conditions with no text: {sorted(blank)}'


class TestOverwriteNote:
    def test_an_always_field_says_always(self):
        assert overwrite_note('tnbc_status') == {'when': 'always'}

    def test_a_conditional_field_names_its_condition(self):
        note = overwrite_note('mipi_risk')
        assert note['when'] == 'sometimes'
        assert note['condition'] == 'for a mantle cell lymphoma patient'

    @pytest.mark.parametrize('field', ['meets_gelf', 'meets_lugano', 'hemoglobin_level'])
    def test_a_field_the_module_never_writes_gets_no_note(self, field):
        """`meets_gelf` and `meets_lugano` by name: both carry
        `is_computed_value` in the attribute mapping and are plain stored
        booleans nothing derives. The draft of this feature that read that flag
        would have hidden the only control that can set them."""
        assert overwrite_note(field) is None

    def test_an_unknown_name_gets_no_note_rather_than_raising(self):
        assert overwrite_note('not_a_field_at_all') is None
        assert overwrite_note(None) is None


class TestAValueThatIsNotStoredAtAll:
    """The register is about what `normalize.py` writes. The KEY is about
    whether a reader's value survives, and those are not the same set.

    `abnormal_kappa_lambda_ratio` and `meets_meas_or_bone_status` are mapped
    attributes, reach a row, and are `@property` with no column and no setter:
    recomputed on every read, and dropped by `_build_in_memory` before they
    reach the instance. They answered `None` — "this value is the reader's" —
    over a field an edit cannot reach at all. Worse than the overwrite case,
    and found by review rather than by this file, which is why it is here now.
    """

    def _read_only_properties(self):
        from trials.services.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING
        from trials.services.patient_info.patient_info import PatientInfo

        out = []
        for name in USER_TO_TRIAL_ATTRS_MAPPING:
            attribute = getattr(PatientInfo, name, None)
            if isinstance(attribute, property) and attribute.fset is None:
                out.append(name)
        return out

    def test_there_are_some_to_talk_about(self):
        """Non-vacuity: if the model stopped having any, every assertion below
        would hold by describing nothing."""
        assert self._read_only_properties()

    def test_none_of_them_reads_as_the_readers_own(self):
        for field in self._read_only_properties():
            assert overwrite_note(field) == {'when': 'never-stored'}, field

    def test_and_they_really_cannot_be_written(self):
        """The claim behind the answer, not just the answer."""
        from trials.services.patient_info.patient_info import PatientInfo

        instance = PatientInfo()
        for field in self._read_only_properties():
            with pytest.raises(AttributeError):
                setattr(instance, field, 'anything')

    def test_a_stored_boolean_is_still_the_readers(self):
        """`meets_gelf` and `meets_lugano` carry `is_computed_value` and are
        plain stored `BooleanField`s that nothing derives — the two the first
        draft would have hidden. They must NOT be caught by this net."""
        assert overwrite_note('meets_gelf') is None
        assert overwrite_note('meets_lugano') is None
