"""Helpers shared by the API layer."""

#: A cell a spreadsheet will evaluate rather than display.
_CSV_FORMULA_PREFIXES = ('=', '+', '-', '@')
#: Leading tab or CR is a trigger in its own right, per the usual guidance, and
#: is checked against the raw value rather than the stripped one.
_CSV_CONTROL_PREFIXES = ('\t', '\r')


def csv_safe_cell(value):
    r"""Neutralise a value a spreadsheet would treat as a formula (CB #4948).

    Trial titles, sponsors and locations are free text written upstream, and the
    export is a file a person opens in Excel or Sheets. A title beginning `=`
    becomes a formula on open — `=HYPERLINK`, `=WEBSERVICE`, or a DDE payload —
    executing in the READER's spreadsheet with their privileges, not ours. The
    reader here is a patient or a clinician, and the file is one they were
    invited to download.

    Prefixing with an apostrophe is the conventional fix: the cell is read as
    text rather than evaluated. On CSV import the apostrophe is visible, unlike
    a value typed into a cell — neutralised, but not invisible.

    `lstrip()` with no argument, deliberately: stripping only ASCII spaces lets
    `"\n=cmd"` and `"\xa0=cmd"` walk past.

    Only strings are touched, which is why the caller must hand numbers over as
    numbers: a genuinely negative `distance` or `matchScore` passed through
    `str()` first would arrive here as text beginning `-` and be quoted into a
    string the spreadsheet no longer sums.
    """
    if not isinstance(value, str):
        return value
    if value.startswith(_CSV_CONTROL_PREFIXES) or value.lstrip().startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value
