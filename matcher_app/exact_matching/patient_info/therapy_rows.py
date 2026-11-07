"""Ported verbatim from CancerBot `trials/services/patient_info/therapy_rows.py`
(cancerbot-org/cancerbot@c0b84f64a, #4951) for the #4994 completeness check
(EXACT #402). Keep byte-identical below this header; see
docs/porting-from-cancerbot.md.
"""

"""One shape for the therapy-line JSON columns (#4951).

`PatientInfo.supportive_therapies` and `.later_therapies` are patient-writable
JSON. Two shapes have been stored over the years: the current
``{'therapy': code, 'date': date}`` row, and a bare therapy code, which three
readers still render (`patient_info_details.get_supportive_therapies`,
`trial_details` likewise) by treating the value itself as the code.

The readers that *matched* on these columns assumed the dict shape and indexed it
directly, so one bare string made trial search and the researcher applicant list
raise `AttributeError` -- permanently, because the value is stored. A row whose
`therapy` was itself a list raised `TypeError: unhashable type` a level deeper.

Normalising in the reader rather than filtering on the write path is deliberate.
There is more than one way into these columns -- the custom `patch_with_custom_fields`
path and the plain `ModelViewSet` routes on the same viewset -- so a write-side
filter alone would leave the readers carrying the risk anyway. It also keeps the
bare-string rows *working* instead of silently dropping them, which would have
turned a loud 500 into a quiet wrong answer from the matcher while the profile
page went on displaying the therapy.
"""

# What a therapy code may be. Anything else in that position is not a code, and
# taking it would only move the crash into `set()`.
_SCALAR = (str, int, float)


def _scalar_or_none(value):
    """The value if it can stand in a row field, otherwise None."""
    if value is None or value == '':
        return None
    if not isinstance(value, _SCALAR) or isinstance(value, bool):
        return None
    return value


def normalize_therapy_row(row):
    """One row in the shape every reader expects, or None if it holds nothing.

    "Nothing" means neither field survives, not "no therapy". `date` is entered
    independently of `therapy` -- the UI sends one field per PATCH, so "pick the
    date, then pick the therapy" is an ordinary two-request flow -- and both
    readers of these columns render a therapy-less row's date. Treating an empty
    `therapy` as an empty row would delete a date the patient had just entered.
    """
    if isinstance(row, str):
        return {'therapy': row, 'date': None} if row else None
    if not isinstance(row, dict):
        return None

    therapy = _scalar_or_none(row.get('therapy'))
    date = _scalar_or_none(row.get('date'))
    if therapy is None and date is None:
        return None
    return {'therapy': therapy, 'date': date}


_EMPTY_ROW = {'therapy': None, 'date': None}


def normalize_therapy_rows(value, keep_positions: bool = False):
    """Whatever is stored in a therapy-line column, as rows the readers can use.

    ``keep_positions`` replaces an unusable row with an empty one instead of
    removing it. The sparse edit the v1 UI sends addresses rows by index
    (``therapy0``, ``date0``, ...), so dropping a bad row ahead of a good one
    would silently retarget the edit at its neighbour. Callers that only read
    want the rows; the caller that patches by index wants the positions.
    """
    if isinstance(value, str):
        # The whole column as a bare string: reachable through the serializer,
        # which declares these fields as CharField over a JSONField. Iterating it
        # would yield one row per character.
        value = [value]
    elif not isinstance(value, (list, tuple)):
        return []

    rows = (normalize_therapy_row(item) for item in value)
    if keep_positions:
        return [row if row else dict(_EMPTY_ROW) for row in rows]
    return [row for row in rows if row]


def therapy_codes(value):
    """The therapy codes in a therapy-line column, ignoring anything unusable.

    A row can now survive normalisation on its date alone, so this filters again
    rather than assuming every returned row names a therapy.
    """
    return [
        row['therapy']
        for row in normalize_therapy_rows(value)
        if row['therapy'] is not None
    ]
