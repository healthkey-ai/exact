"""SME-approved corrections to the genetic-mutation catalog (#5297).

The seeded mutation examples are a clinical vocabulary, not free text: a wrong
cDNA either does not encode the labelled protein or uses a base that is not at
that position. The first pass (#5297, migrations 0456/0457) corrected the
unambiguous rows and left seven pairs for the medical reviewer. Their decision
(issue comment, 2026-11-08) is recorded here so the loader, the migration and
the usage report cannot drift apart:

- ``R249S`` is kept, but as the standard hotspot change ``c.747G>T`` (recorded
  in ClinVar on NM_000546.6 as p.Arg249Ser).
- ``Q172H``/``V172F``, ``R282S``, ``R302W``, ``Q234*``, ``R289W`` and ``Y296*``
  are removed from the dropdown: the named amino-acid change does not match the
  reference sequence and none is a standard example worth listing.

Only the catalog changes. Stored patient and trial answers are strings in JSON,
not foreign keys; they keep their value, and ``report_mutation_catalog_usage``
lists the records that still hold an affected value for the reviewer.
"""

#: Old catalog title -> the title that replaces it.
RECODED = {
    'c.746C>G (R249S)': 'c.747G>T (R249S)',
}

#: Titles removed from the dropdown by the SME decision.
REMOVED = (
    'c.514G>T (Q172H)',
    'c.844C>A (R282S)',
    'c.904C>T (R302W)',
    'c.700C>T (Q234*)',
    'c.865C>T (R289W)',
    'c.886C>T (Y296*)',
)

#: Historical catalog titles retired by #5297, with the title that replaced the
#: example when it was recoded and ``None`` when it was removed.
#:
#: Display and report only. The frozen 2026-11-08 decision (``RECODED`` /
#: ``REMOVED``) is what migration 0459 deletes; this wider registry exists so
#: an answer saved while an earlier spelling was seeded still renders its
#: historical label and appears in ``report_mutation_catalog_usage``.
#:
#: It lists every title the loader ever seeded. Migration 0456 also deleted
#: curator-created rows matching the broader ``PIK3CA_G1049_BLOCK`` regex; those
#: were never seeded, so they are not reported.
RETIRED = {
    **RECODED,
    **{title: None for title in REMOVED},
    # 0456: wrong cDNA (>), or a duplicate row for the same variant.
    'c.472G>T (R158L)': 'c.473G>T (R158L)',
    'c.476G>A (R158H)': 'c.473G>A (R158H)',
    'c.743G>T (R248W)': 'c.742C>T (R248W)',
    'c.742C>T (R248Q)': 'c.743G>A (R248Q)',
    'c.1607T>A (L536Q)': 'c.1607_1608delinsAG (L536Q)',
    'c.1609A>C (Y537S)': 'c.1610A>C (Y537S)',
    # 0457: the ten unambiguous corrections.
    'c.658T>C (Y220C)': 'c.659A>G (Y220C)',
    'c.578C>T (H193Y)': 'c.577C>T (H193Y)',
    'c.637C>A (R213Q)': 'c.638G>A (R213Q)',
    'c.500C>A (Q167K)': 'c.499C>A (Q167K)',
    'c.489A>G (Y163C)': 'c.488A>G (Y163C)',
    'c.818G>C (R273C)': 'c.817C>T (R273C)',
    'c.847G>C (R282G)': 'c.844C>G (R282G)',
    'c.796G>T (G266V)': 'c.797G>T (G266V)',
    'c.877A>G (K292E)': 'c.874A>G (K292E)',
    'c.1637A>T (p.Q546K)': 'c.1636C>A (p.Q546K)',
    # 0456: the generated PIK3CA G1049 block (c.3146-c.3157), all removed.
    'c.3146G>C (p.G1049A)': None,
    'c.3146G>T (p.G1049V)': None,
    'c.3147G>A (p.G1049D)': None,
    'c.3147G>C (p.G1049E)': None,
    'c.3147G>T (p.G1049F)': None,
    'c.3148G>A (p.G1049N)': None,
    'c.3148G>C (p.G1049T)': None,
    'c.3148G>T (p.G1049Y)': None,
    'c.3149G>A (p.G1049C)': None,
    'c.3149G>C (p.G1049W)': None,
    'c.3149G>T (p.G1049H)': None,
    'c.3150G>A (p.G1049L)': None,
    'c.3150G>C (p.G1049M)': None,
    'c.3150G>T (p.G1049I)': None,
    'c.3151G>A (p.G1049K)': None,
    'c.3151G>C (p.G1049R)': None,
    'c.3151G>T (p.G1049S)': None,
    'c.3152G>A (p.G1049T)': None,
    'c.3152G>C (p.G1049V)': None,
    'c.3152G>T (p.G1049Y)': None,
    'c.3153G>A (p.G1049D)': None,
    'c.3153G>C (p.G1049E)': None,
    'c.3153G>T (p.G1049F)': None,
    'c.3154G>A (p.G1049N)': None,
    'c.3154G>C (p.G1049T)': None,
    'c.3154G>T (p.G1049Y)': None,
    'c.3155G>A (p.G1049C)': None,
    'c.3155G>C (p.G1049W)': None,
    'c.3155G>T (p.G1049H)': None,
    'c.3156G>A (p.G1049L)': None,
    'c.3156G>C (p.G1049M)': None,
    'c.3156G>T (p.G1049I)': None,
    'c.3157G>A (p.G1049K)': None,
    'c.3157G>C (p.G1049R)': None,
    'c.3157G>T (p.G1049S)': None,
}


#: The SME's validated spelling of ``V422del`` (#5297, 2026-11-13) and the
#: stored option code it must keep. ``c.1264_1266delGTG`` and
#: ``c.1265_1267del`` are the same in-frame deletion; the 3' shift changes the
#: label, not the variant, and the code is what patient/trial answers hold.
V422DEL_TITLE = 'c.1265_1267del (V422del)'
V422DEL_CODE = 'c.1264_1266delgtg_(v422del)'

#: Titles whose stored code does not follow from the label.
STABLE_TITLES = {V422DEL_TITLE: V422DEL_CODE}

#: Label spellings that changed without changing the stored code (V422del).
#: Display and report only: the row still exists, under the corrected label, so
#: it is not in ``old_titles()``.
#: EXACT-only second entry: the loader converges a row holding the corrected
#: label under the derived code onto ``V422DEL_CODE``, so an answer stored
#: against that derived code keeps a label.
PREVIOUS_LABELS = {
    'c.1264_1266delGTG (V422del)': V422DEL_TITLE,
    'c.1265_1267del_(v422del)': V422DEL_TITLE,
}


def code(title: str) -> str:
    """The stored form of a catalog title (see ``MutationCode.code``).

    Not a total transform: a row whose identity survives a relabel is listed in
    ``STABLE_TITLES`` and keeps its original code.
    """
    if title in STABLE_TITLES:
        return STABLE_TITLES[title]
    return title.replace('>', '_').replace(' ', '_').lower()


def old_titles() -> tuple:
    """Every historical title a stored answer may still hold.

    Includes the 2026-11-08 decision values and the examples retired by the
    earlier #5297 passes (migrations 0456/0457).
    """
    return tuple(RETIRED)


def _normalize(value: str) -> str:
    # `*` is a stop codon, not punctuation: dropping it would make `Q234R`
    # look like the removed `Q234*` in the composite-substring check below.
    return ''.join(
        ch if ch.isalnum() else ('stop' if ch == '*' else '')
        for ch in value.lower()
    )


def _title_parts(title: str) -> tuple:
    return (title, title.lower(), code(title), _normalize(title))


def _protein(title: str) -> str:
    return title.rsplit('(', 1)[-1].rstrip(')').lower().removeprefix('p.')


#: Precomputed once: the report walks every stored string on patient, trial and
#: PROMOP rows, so rebuilding split/normalize work per candidate was measurably
#: slower (and the registry only grows).
_TITLE_INDEX = tuple(_title_parts(title) for title in old_titles())
_LABELS_BY_CODE = {code(title): title for title in old_titles()}
_PREVIOUS_LOOKUP = {old.lower(): new for old, new in PREVIOUS_LABELS.items()}
_RETIRED_PROTEINS = frozenset(_protein(title) for title in old_titles())


def retired_protein(value: str) -> bool:
    """Whether a bare protein token names a protein the catalog retired.

    PROMOP free text stores findings as ``p.R249S``/``R249S``; the protein alone
    cannot select one cDNA spelling, so the report counts these separately
    instead of guessing a title.
    """
    if not isinstance(value, str):
        return False
    token = value.strip().lower()
    if token.startswith('p.'):
        token = token[2:]
    return token in _RETIRED_PROTEINS


def label_for_stored_code(value: str) -> str | None:
    """A human label for a stored answer whose catalog row no longer exists.

    The row is gone from the dropdown, but a patient or trial answer that was
    saved while it existed must still render as something a reader can
    recognise. Accepts the stored code, the catalog title, and the composite
    origin form `origin__<code>` the mutation-origin options use.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    needle = value.strip()
    lowered = needle.lower()
    if lowered in _PREVIOUS_LOOKUP:
        return _PREVIOUS_LOOKUP[lowered]
    if needle in _LABELS_BY_CODE:
        return _LABELS_BY_CODE[needle]
    if lowered in _LABELS_BY_CODE:
        return _LABELS_BY_CODE[lowered]
    for title, _lowered, _variant, _normalized in _TITLE_INDEX:
        if needle == title:
            return title
    # `somatic__c.746c_g_(r249s)`: the origin prefix is not part of the catalog
    # title, and the composite spelling replaces `>` with `_` as well.
    tail = needle.split('__')[-1]
    for title, _lowered, variant, _normalized in _TITLE_INDEX:
        if tail == variant:
            origin = needle.split('__')[0] if '__' in needle else ''
            return f'{origin} — {title}' if origin else title
    return None


def with_stored_labels(options, values) -> list:
    """Append an option for each stored value the current catalog dropped.

    The option is added only to the list handed to the record that holds it —
    never to the global dropdown — so the value stays visible and unselectable
    elsewhere.
    """
    present = {option.get('value') for option in options or []}
    out = list(options or [])
    for value in values or []:
        if not isinstance(value, str) or not value or value in present:
            continue
        label = label_for_stored_code(value)
        if label is None:
            continue
        out.append({'value': value, 'label': label})
        present.add(value)
    return out


def matched_title(value: str) -> str | None:
    """The affected catalog title a stored value refers to, if any.

    Stored answers use either the code (`c.746c>g_(r249s)`) or the catalog
    title (`c.746C>G (R249S)`). Compare case-insensitively and accept either.
    """
    if not isinstance(value, str):
        return None
    needle = value.strip().lower()
    if not needle:
        return None
    if needle in _PREVIOUS_LOOKUP:
        return _PREVIOUS_LOOKUP[needle]
    normalized = _normalize(needle)
    for title, lowered, variant, normalized_title in _TITLE_INDEX:
        if needle in {lowered, variant}:
            return title
        # Contiguous after normalisation: the cDNA and protein stay together,
        # so two current variants in one string cannot form a retired pair.
        if normalized_title in normalized:
            return title
    return None

