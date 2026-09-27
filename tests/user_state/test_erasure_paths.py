"""Reachability, on the shapes this app does not have yet.

Erasure is only as complete as this function. Every defect found in it so far
was about a shape nobody had built — a child declared above its parent, a
many-to-many, two links to the same identity — so these are graphs rather
than migrations: the resolver is pure, and a stub with the two attributes it
reads is enough to state the rule it has to obey.
"""
import itertools

import pytest

from user_state.models import erasure_paths


class Identity:
    pass


class Link:
    """What the resolver reads off a field: a name, a target, concreteness.

    The stub is only as good as its fidelity to Django, and that is the one
    thing a stub can get wrong invisibly. Verified against real metadata
    rather than assumed: a declared `ManyToManyField` reports
    `concrete=True` (`Group.permissions`), its reverse side reports
    `concrete=False` (`Group.user`), and a plain reverse accessor does too
    (`Identity.trial_search_preferences`). So `concrete` is the right
    discriminator between "this table has a column pointing there" and "that
    table points here", which is what the resolver needs.
    """

    def __init__(self, name, related_model, concrete=True):
        self.name = name
        self.related_model = related_model
        self.concrete = concrete


def model(name, *links):
    meta = type('Meta', (), {'get_fields': staticmethod(lambda: list(links))})
    return type(name, (), {'_meta': meta()})


def test_a_direct_link_is_the_field_name():
    prefs = model('Prefs', Link('identity', Identity))

    assert erasure_paths([prefs], Identity) == {prefs: ['identity']}


def test_a_child_reaches_the_identity_through_its_parent():
    prefs = model('Prefs', Link('identity', Identity))
    note = model('Note', Link('prefs', prefs))

    paths = erasure_paths([note, prefs], Identity)

    assert paths[note] == ['prefs__identity']


@pytest.mark.parametrize('order', range(6))
def test_the_answer_does_not_depend_on_declaration_order(order):
    # The defect this function was rewritten for: one ordered pass decided
    # reachability by source order, so a child declared above its parent came
    # out "unreachable" and stopped erasure for every identity in the service
    # until somebody moved a class. Every permutation, not the two obvious
    # ones — three models is where a single pass starts being wrong in more
    # than one way.
    prefs = model('Prefs', Link('identity', Identity))
    note = model('Note', Link('prefs', prefs))
    tag = model('Tag', Link('note', note))
    models = list(itertools.permutations([prefs, note, tag]))[order]

    paths = erasure_paths(list(models), Identity)

    assert paths[prefs] == ['identity']
    assert paths[note] == ['prefs__identity']
    assert paths[tag] == ['note__prefs__identity']


def test_a_many_to_many_is_a_link():
    # `_meta.fields` omits them, and a model linked only that way used to be
    # told "nothing links it to Identity" — untrue, and fatal for everyone.
    shared = model('Shared', Link('identities', Identity))

    assert erasure_paths([shared], Identity) == {shared: ['identities']}

def test_both_links_are_used_when_a_model_has_two():
    # Otherwise one is erased by the query and the other by a cascade the
    # receipt cannot account for — and a second link with PROTECT would make
    # every erasure raise.
    shared = model('Shared', Link('owner', Identity), Link('referred_by', Identity))

    assert erasure_paths([shared], Identity) == {shared: ['owner', 'referred_by']}


def test_both_paths_are_used_when_a_model_reaches_through_two_parents():
    # The same rule one hop further out, which the direct case above does not
    # exercise: a row reached only by its second parent would survive the
    # query and die by cascade, absent from the receipt.
    prefs = model('Prefs', Link('identity', Identity))
    other = model('Other', Link('identity', Identity))
    note = model('Note', Link('prefs', prefs), Link('other', other))

    paths = erasure_paths([note, prefs, other], Identity)

    assert sorted(paths[note]) == ['other__identity', 'prefs__identity']


@pytest.mark.parametrize('order', range(24))
def test_a_second_route_through_a_deeper_parent_is_not_lost(order):
    # The case the two-parent test above cannot reach: both of ITS parents
    # are direct, so they resolve in the same pass and no route is pending
    # when the child is settled. Here one parent is a hop further out, so a
    # resolver that fixed the child as soon as the first parent was known
    # would drop the other route — silently, since those rows would then die
    # by cascade and be absent from the per-table counts.
    #
    # Every permutation, because only some of them expose it: the first
    # version of this test picked one order, and it happened to be an order
    # where the naive resolver got the right answer anyway.
    prefs = model('Prefs', Link('identity', Identity))
    other = model('Other', Link('identity', Identity))
    mid = model('Mid', Link('other', other))
    note = model('Note', Link('prefs', prefs), Link('mid', mid))
    models = list(itertools.permutations([prefs, other, mid, note]))[order]

    paths = erasure_paths(list(models), Identity)

    assert sorted(paths[note]) == ['mid__other__identity', 'prefs__identity']
    assert paths[mid] == ['other__identity']


def test_a_model_nothing_links_refuses_the_whole_operation():
    # Before the transaction, and naming all of them: a partial erasure
    # destroys data and leaves the subject on file.
    prefs = model('Prefs', Link('identity', Identity))
    orphan = model('Orphan')

    with pytest.raises(RuntimeError, match='Orphan'):
        erasure_paths([prefs, orphan], Identity)


def test_a_reverse_relation_is_not_a_link():
    # Django reports reverse accessors in `get_fields()` too. Following one
    # would "reach" the identity from a table that has no column pointing at
    # it, and the delete would silently match nothing.
    prefs = model('Prefs', Link('identity', Identity))
    reverse_only = model('ReverseOnly', Link('note_set', prefs, concrete=False))

    with pytest.raises(RuntimeError, match='ReverseOnly'):
        erasure_paths([prefs, reverse_only], Identity)
