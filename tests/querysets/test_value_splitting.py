"""One value, split the same way everywhere `trials.querysets.trial` reads it.

What this is about, and what it is not. EXACT had two splitting rules over
one dispatch table: a bare `value.split(",")` for 16 fields and a stripping
helper for 11 more. #588 made it one.

The live break that closed: PROMOP `", ".join(...)`s `staging_modalities`
and `protein_expressions`, so their second and later values arrived with a
leading space and matched nothing. Two fields, today, for any patient who
answered twice.

Not `cytogenic_markers` — that one never reaches this module at all (#590).
Not `molecular_markers`, which the producer joins with `"; "`, and least of
all `languages_skills`, which PROMOP serves as `"; "` between languages and
`", "` inside each one; when PROMOP sends its language booleans, `resolve.py`
rebuilds it as codes, or drops it when they are all null, before `_csv` (#591).

The tests below assert the rule, not the story, and that includes the two
inputs the rule gets wrong.
"""
import random

import pytest

from trials.querysets.trial import _csv


class TestTheSeparatorPromopEmits:
    def test_a_space_after_the_comma_is_not_part_of_the_value(self):
        # `', '.join(staging_vals)` — patient_record_service.py:2840.
        assert _csv("ct, mri") == ["ct", "mri"]

    def test_no_space_reads_the_same(self):
        assert _csv("ct,mri") == ["ct", "mri"]

    def test_several_values(self):
        assert _csv("cd38, cd138, cd56") == ["cd38", "cd138", "cd56"]

    def test_a_single_value_is_one_value(self):
        assert _csv("ct") == ["ct"]

    def test_a_non_string_scalar_is_stringified(self):
        # Both references do this (`cytogenetics.py:50`, `splitJoined`), and
        # two call sites in this module used to. Without it
        # `{"concomitantMedications": 5}` — which `_coerce_json_fields` can
        # also produce for SCT history — is a TypeError out of `re.split`
        # instead of a 200 with a no-op filter.
        assert _csv(5) == ["5"]

    def test_nothing_is_no_values(self):
        assert _csv("") == []
        assert _csv(None) == []


class TestAListStaysAList:
    # `str()` here would produce ["['a'", "'b']"] — two tokens that match
    # nothing, silently. Before #588 this raised AttributeError and the
    # request 500'd, which was wrong but at least loud. PROMOP's own reader
    # (`cytogenetics.py`) and the editor (`splitJoined`) both branch on a
    # list first; this is the third of the three.
    def test_a_list_is_its_own_values(self):
        assert _csv(["del17p13", "t414"]) == ["del17p13", "t414"]

    def test_a_one_item_list_is_one_value(self):
        assert _csv(["her2_plus"]) == ["her2_plus"]

    def test_items_are_stripped_and_stringified(self):
        assert _csv((" a ", 2)) == ["a", "2"]

    def test_a_comma_inside_an_item_is_not_a_separator(self):
        # The caller already decided where the values are.
        assert _csv(["a,b"]) == ["a,b"]


class TestAMarkerThatContainsAComma:
    # Guarding the shape, not repairing a live break: no value any of the 29
    # fields this rule reads carries a comma INSIDE its parens today. See
    # TestTheBracketRuleDoesNotCoverTheValueThatExists for the commas that
    # are really there, which the parens do not help with.
    def test_brackets_hold_it_together(self):
        assert _csv("inv(3)(q21,q26)") == ["inv(3)(q21,q26)"]

    def test_beside_another_marker(self):
        assert _csv("inv(3)(q21,q26), del17p13") == ["inv(3)(q21,q26)", "del17p13"]

    def test_two_bracketed_codes(self):
        assert _csv("t(4;14), t(14;16)") == ["t(4;14)", "t(14;16)"]


class TestTheBracketRuleDoesNotCoverTheValueThatExists:
    """The commas that are really in the data are not inside any parens.

    The lookahead only holds a comma between `(` and `)`. What PROMOP
    actually seeds is prose with a comma in it — sometimes next to parens,
    sometimes with none at all — so the rule tears these exactly as the old
    one did. Not a regression (old and new tokenize every seeded choice of
    all 29 fields identically) but the guard does not cover the shape that
    is there (#591).
    """

    def test_a_seeded_stage_choice_is_still_torn_in_half(self):
        # ctomop 0217_seed_dropdown_field_choices.py:71, a real
        # `distant_metastasis_stage` choice.
        assert _csv("M0(i+): No metastasis on scans, but cancer cells found") == [
            "M0(i+): No metastasis on scans",
            "but cancer cells found",
        ]

    def test_and_so_is_a_tumor_stage_choice_with_no_parens_at_all(self):
        # `:37` in the same migration. Nothing here for the lookahead to
        # hold on to — the bracket rule is beside the point for it.
        assert _csv("Tx: Primary Tumor, cannot be assessed") == [
            "Tx: Primary Tumor",
            "cannot be assessed",
        ]


class TestWhereTheRuleIsWrongAndWeKnowIt:
    """The lookahead is a heuristic. These lock in what it actually does.

    It is PROMOP's regex character for character, so agreeing with it is the
    point and diverging would be the defect — but it is wrong in both
    directions and a reader should find that here rather than in production.
    """

    def test_a_stray_close_paren_suppresses_a_real_split(self):
        # The merge direction: two values become one token. This is the
        # dangerous one, because nothing downstream can tell.
        assert _csv("ct, mri)") == ["ct, mri)"]

    def test_a_nested_bracket_is_split_anyway(self):
        # The lookahead only looks as far as the first `)`.
        assert _csv("f(a, g(b))") == ["f(a", "g(b))"]


class TestEmptyPartsAreKept:
    # Deliberate, and the opposite of what it looks like it should be.
    # `eligible_for_required_lists` returns `self` — no filter at all — when
    # the value list is `[]`. Dropping empties would turn a whitespace-only
    # answer from "only trials that require nothing" into "every trial",
    # which is the widening direction.
    def test_a_stray_comma_still_produces_a_part(self):
        assert _csv("ct,,mri") == ["ct", "", "mri"]

    def test_a_trailing_comma_still_produces_a_part(self):
        assert _csv("ct, ") == ["ct", ""]

    def test_whitespace_only_is_one_empty_part_not_no_parts(self):
        assert _csv(" ") == [""]

    @pytest.mark.parametrize("value", [0, False])
    def test_falsy_non_strings_are_no_values(self, value):
        # The guard runs before the split, so these never reach the regex.
        assert _csv(value) == []


class TestWhatIsNotAValueAtAll:
    """A container stays loud — `test_a_container_is_refused_at_either_level`
    below generates the shapes; this is the edge the generator cannot reach.
    """

    def test_an_empty_mapping_is_still_no_values(self):
        # The truthiness guard runs first, and "" / None / {} all mean the
        # patient answered nothing.
        assert _csv({}) == []


# ---------------------------------------------------------------------------
# The invariant, written down.
#
# Three review rounds each found one defect in `_csv`, and all three were one
# question wearing different clothes: what does it do with a value that is
# not a joined string? Round 1 said a list, round 2 a non-string scalar,
# round 3 a mapping. Each was fixed as its own branch, which is how a fourth
# round happens. A fourth round then found the round-3 fix was true only of
# the top level — `[{'a': 1}]` still rendered.
#
# So, stated once instead of discovered again:
#
#   `_csv` accepts exactly two shapes — a STRING holding joined values, or a
#   SEQUENCE that is already the values. It returns a list of stripped
#   strings, it never changes how many values a sequence had, keeps empty
#   parts, and refuses a container at EITHER level rather than rendering it.
#
# The cases below are generated, seeded (so a failure is reproducible from
# the printed case), and every property here was checked to fail under at
# least one ablation before being kept. Two earlier drafts could not fail at
# all and were rewritten; that check is the point of the harness.
# ---------------------------------------------------------------------------


# Atoms for the JOINED-STRING half. All are comma-free except the bracketed
# marker, whose comma is inside its parens — which is what makes the count
# property below well-defined. The two prose atoms are the real seeded
# choices with their trailing clause cut off for that reason; the comma
# shape that is actually live in the data (#591) is covered by the
# hand-written tests above, not here.
_ATOMS = [
    "ct",
    "mri",
    "del17p13",
    "t(11;14)(q13;q32)",
    "inv(3)(q21,q26)",
    "cd38",
    "M0(i+): No metastasis on scans",
    "Tx: Primary Tumor",
    "lenalidomide-based_therapy",
]
_PLAIN_ATOMS = [a for a in _ATOMS if "," not in a and "(" not in a]

# Atoms for the SEQUENCE half. A sequence's items are already the values, so
# anything may be inside one — including a comma outside brackets, which no
# joined string in the pool above has, and a non-string, which nothing above
# exercises. Both were added because a mutation survived without them.
_ITEMS = _ATOMS + ["a,b", "ct, mri", 5, 3.5]

_SEPARATORS = [",", ", ", ",  ", " , ", ",\t"]


def _cases(seed, n=200):
    """(tokens, the string PROMOP would have joined them into)."""
    rng = random.Random(seed)
    for _ in range(n):
        tokens = [rng.choice(_ATOMS) for _ in range(rng.randint(1, 5))]
        joined = ""
        for i, token in enumerate(tokens):
            if i:
                joined += rng.choice(_SEPARATORS)
            joined += token
        yield tokens, joined


def _sequences(seed, n=200):
    rng = random.Random(seed)
    for _ in range(n):
        yield [rng.choice(_ITEMS) for _ in range(rng.randint(1, 5))]


def _with_gaps(seed, n=200):
    """Joined strings carrying empty parts: leading, trailing, doubled."""
    rng = random.Random(seed)
    for _ in range(n):
        tokens = [rng.choice(_PLAIN_ATOMS) for _ in range(rng.randint(1, 3))]
        gaps = [""] * rng.randint(1, 3)
        parts = tokens + gaps
        rng.shuffle(parts)
        yield parts, ",".join(parts)


class TestTheShapeContract:
    def test_every_part_comes_back_stripped(self):
        for _, joined in _cases(seed=588):
            for part in _csv(joined):
                assert part == part.strip(), repr(joined)

    def test_every_part_is_a_string(self):
        for sequence in _sequences(seed=589):
            parts = _csv(sequence)
            assert all(isinstance(p, str) for p in parts), repr(sequence)

    def test_a_sequence_keeps_its_own_count(self):
        # The caller already decided where the values are. Whatever is
        # inside an item — a comma, a bracket, a semicolon — `_csv` does not
        # get a second opinion about it.
        for sequence in _sequences(seed=590):
            assert len(_csv(sequence)) == len(sequence), repr(sequence)
            assert len(_csv(tuple(sequence))) == len(sequence), repr(sequence)

    def test_a_joined_list_of_plain_codes_round_trips(self):
        # The producer joins with ", " and this reads it back. Stated as
        # behaviour rather than by re-running the regex, so it can fail.
        rng = random.Random(591)
        for _ in range(200):
            tokens = [rng.choice(_PLAIN_ATOMS) for _ in range(rng.randint(1, 5))]
            assert _csv(", ".join(tokens)) == tokens

    def test_a_value_with_no_comma_is_one_value(self):
        for atom in _ATOMS:
            if "," in atom:
                continue
            assert _csv(atom) == [atom]
            assert _csv(f"  {atom}  ") == [atom]

    def test_joining_n_values_and_reading_them_back_gives_n_values(self):
        # Count, not content, so one assertion catches both over- and
        # under-splitting. `inv(3)(q21,q26)` is in the pool precisely
        # because it carries a comma of its own: a rule that counts that
        # comma as a separator returns more values than were joined.
        for tokens, joined in _cases(seed=592):
            assert len(_csv(joined)) == len(tokens), repr(joined)

    def test_an_empty_part_is_still_a_part(self):
        # The widening direction, generated rather than hand-picked: drop
        # these and a whitespace-only answer stops filtering at all, because
        # `eligible_for_required_lists` reads `[]` as "no filter".
        for parts, joined in _with_gaps(seed=593):
            assert _csv(joined) == parts, repr(joined)

    def test_a_container_is_refused_at_either_level(self):
        for value in [{"a": 1}, {"x", "y"}, frozenset({"x"}),
                      [{"a": 1}], [{"x", "y"}], [["a", "b"]], (("a",),)]:
            with pytest.raises(TypeError):
                _csv(value)
