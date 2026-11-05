"""Whether a recorded therapy history is exact or only a lower bound (#5483).

A patient can record lines of therapy without being able to say the list is
complete. Until they confirm it, the recorded count is "at least N", and a
matcher must not turn an unprovable upper bound into an eligible verdict, nor an
unprovable lower bound into an exclusion.

The flag lives on the patient surface rather than in this package: a host that
does not track completeness (CancerBot's own `PatientInfo`) has no such
attribute, and there the explicitly entered count stays exact — the behaviour
this package has always had. A surface that does track it (CancerBot's PROMOP
`PatientRecord`) carries `therapy_history_complete`: True only after the patient
confirmed, NULL while unconfirmed.
"""


def history_is_complete(patient_info) -> bool:
    """True when the recorded line count may be compared as an exact number.

    Missing attribute -> True: a surface without the concept has no way to state
    a lower bound, and treating every such patient as incomplete would silently
    change long-standing behaviour. Present but not True (NULL/False) -> False.
    """
    missing = object()
    value = getattr(patient_info, 'therapy_history_complete', missing)
    if value is missing:
        return True
    return value is True
