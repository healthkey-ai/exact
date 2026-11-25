"""A copied presentation value, not another matcher or evidence/trust contract.

The server prepares BOTH patient-facing views from one final result. This seam
never resolves a patient, reconstructs metadata, evaluates criteria or falls back
for missing rows. Callers still own authorization, publication and completeness.
"""
from dataclasses import dataclass
import json
import math

from trials.services.attribute_names import AttributeNames
from trials.services.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING

_MAX_BYTES = 1_000_000
_STATUSES = {'matched', 'not_matched', 'unknown', 'not_evaluated', 'unavailable', 'unsupported'}
#: `save()` only assigns these when derivation succeeds; otherwise an authored
#: answer survives. They are not unconditional top-level read-only verdicts.
#: Mirrored from CB, whose model owns the derivation.
_MANUAL_FALLBACK_DERIVED_FIELDS = {
    'progression', 'stem_cell_transplant_history', 'estimated_glomerular_filtration_rate',
}


def _computed_names(patient_info):
    """Static read-only names for the supplied patient schema.

    EXACT carries no `PatientInfo` model, so unlike CB there is no local
    `DERIVED_FIELDS_IN_SAVE` / `PI_ATTR_READONLY` registry to read. The host
    patient surface owns that schema and may declare `DERIVED_FIELDS_IN_SAVE`;
    when it does, its unconditional entries apply exactly as CB's do. The rest
    comes from the shared mapping's read-only policy, plus calculated aliases.
    """
    declared = set(getattr(type(patient_info), 'DERIVED_FIELDS_IN_SAVE', ()) or ())
    derived = declared - _MANUAL_FALLBACK_DERIVED_FIELDS
    names = derived | {'match_score', 'goodness_score', 'distance', 'distance_penalty'}
    for attr, metadata in USER_TO_TRIAL_ATTRS_MAPPING.items():
        # ULN ratios are calculated even when their source lab is editable.
        for bound in ('min', 'max'):
            if 'uln_attr_' + bound in metadata:
                names.add(metadata['uln_attr_' + bound])
        # Match the mapping's UI policy for manually controlled answers, but
        # never let it unlock the host's persisted-derived entries. EXACT's older
        # `get_user_details` lacks this `under_user_control` exception; CancerBot,
        # the upstream source of truth, has it and pins it with
        # `test_manually_entered_lugano_and_gelf_values_remain_editable`.
        default_readonly = (
            metadata.get('is_computed_value', False) and not metadata.get('under_user_control', False)
        )
        if attr not in derived and not metadata.get('user_value_readonly', default_readonly):
            continue
        names.add(attr)
        fields = metadata['attr']
        names.update(fields if isinstance(fields, list) else [fields])
        for bound in ('min', 'max'):
            if metadata['type'] in (bound + '_value', 'min_max_value'):
                alias = metadata.get('attr_' + bound)
                if not isinstance(alias, str):
                    # Legacy derives `<attr>_min` / `<attr>_max` when the mapping
                    # omits an explicit alias (e.g. `bmi`). A list `attr` has no
                    # single derived name, so nothing is invented for it.
                    if isinstance(fields, list):
                        continue
                    alias = f'{fields}_{bound}'
                names.add(alias)
    # The potential template combines Min/Max rows under the Min name with
    # its suffix removed. Guard that presentation alias as well as each bound.
    names.update(name.removesuffix('_min') for name in tuple(names) if name.endswith('_min'))
    # Legacy source subforms mark every persisted-derived field read-only,
    # including conditional ones; only the top-level authoring fallback differs.
    return names, names | declared


def _json_snapshot(value):
    # Check BEFORE encoding: custom objects, cyclic/deep trees, huge strings and
    # non-finite numbers cannot cause unbounded serialization work.
    stack, count, encoded_bytes = [(value, 0)], 0, 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 32 or count > 50000:
            raise ValueError('presentation_budget')
        kind = type(item)
        if kind is dict:
            if count + len(stack) + 2 * len(item) > 50000:
                raise ValueError('presentation_budget')
            encoded_bytes += 2 + max(len(item) - 1, 0) + len(item)  # braces, commas, colons
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError('presentation_json')
                stack.extend(((key, depth + 1), (child, depth + 1)))
        elif kind is list:
            if count + len(stack) + len(item) > 50000:
                raise ValueError('presentation_budget')
            encoded_bytes += 2 + max(len(item) - 1, 0)
            stack.extend((child, depth + 1) for child in item)
        elif kind is str:
            if len(item) > _MAX_BYTES:
                raise ValueError('presentation_budget')
            try:
                encoded_bytes += len(item.encode('utf-8')) + 2
            except UnicodeError as error:
                raise ValueError('presentation_json') from error
            # JSON adds one byte for short escapes, five for other controls.
            encoded_bytes += sum(1 if char in '"\\\b\f\n\r\t' else 5 if ord(char) < 32 else 0 for char in item)
        elif kind is float:
            if not math.isfinite(item):
                raise ValueError('presentation_json')
            encoded_bytes += len(repr(item))
        elif kind is int:
            if item.bit_length() > 4096:
                raise ValueError('presentation_budget')
            encoded_bytes += len(str(item))
        elif kind is bool:
            encoded_bytes += 4 if item else 5
        elif item is None:
            encoded_bytes += 4
        else:
            raise ValueError('presentation_json')
        if encoded_bytes > _MAX_BYTES:
            raise ValueError('presentation_budget')
    try:
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(',', ':'))
        if len(encoded.encode('utf-8')) > _MAX_BYTES:
            raise ValueError('presentation_budget')
    except (TypeError, UnicodeError, OverflowError) as error:
        raise ValueError('presentation_json') from error
    return encoded


def _rows(rows, readonly_names, *, eligibility=False, subform=False):
    if type(rows) is not list:
        raise ValueError('presentation_rows')
    computed = readonly_names[1] if subform else readonly_names[0]
    for row in rows:
        if type(row) is not dict or type(row.get('name')) is not str or not row['name']:
            raise ValueError('presentation_row')
        if eligibility and (type(row.get('matchingType')) is not str or row['matchingType'] not in _STATUSES):
            raise ValueError('presentation_status')
        if 'matchingType' in row and not eligibility and row['matchingType'] is not None:
            if type(row['matchingType']) is not str:
                raise ValueError('presentation_status')
        # Static UI metadata only; no user-details/value-options/patient reads.
        # Check patient and Trial aliases only for read-only protection. Source
        # metadata itself must already be supplied, not reconstructed from names.
        patient_field = row.get('ufield')
        if patient_field is not None and (type(patient_field) is not str or not patient_field):
            raise ValueError('presentation_patient_field')
        attr = AttributeNames.get_by_camel_case(patient_field or row['name'])
        readonly_key = 'readonly' if subform else 'ureadonly'
        if readonly_key in row and type(row[readonly_key]) is not bool:
            raise ValueError('presentation_readonly')
        if (attr in computed or AttributeNames.get_by_camel_case(row['name']) in computed
                or row.get('subform_details') is not None):
            if row.get(readonly_key) is not True:
                raise ValueError('presentation_computed_readonly')
        if row.get('subform_details') is not None:
            _rows(row['subform_details'], readonly_names, subform=True)


@dataclass(frozen=True, init=False, eq=False, repr=False)
class SuppliedTrialPresentation:
    """Complete prepared views; binding guards accidental cross-context reuse.

    Identity binding is NOT authorization. The caller must authorize the
    patient and prepare source/subform metadata from the same evaluated request
    context. There is no HTTP payload/settings/producer hook that creates these
    values.
    """
    _trial_id: int
    _patient_info: object
    _snapshot: str

    def __init__(self, trial_id, patient_info, *, potential, grouped, group_names):
        if type(trial_id) is not int or trial_id <= 0 or patient_info is None:
            raise ValueError('presentation_binding')
        payload = {'potential': potential, 'grouped': grouped, 'group_names': group_names}
        encoded = _json_snapshot(payload)
        # Validate the copy, not mutable caller data.
        payload = json.loads(encoded)
        potential, grouped, group_names = payload['potential'], payload['grouped'], payload['group_names']
        if type(potential) is not dict or set(potential) != {'general', 'trialEligibilityAttributes'}:
            raise ValueError('presentation_potential')
        readonly_names = _computed_names(patient_info)
        _rows(potential['general'], readonly_names)
        _rows(potential['trialEligibilityAttributes'], readonly_names, eligibility=True)
        if type(grouped) is not dict or {'admin', 'ignore'} & grouped.keys():
            raise ValueError('presentation_grouped')
        for group, rows in grouped.items():
            _rows(rows, readonly_names, eligibility=group != 'general')
        if type(group_names) is not list:
            raise ValueError('presentation_groups')
        names = []
        for group in group_names:
            if (type(group) is not dict or set(group) != {'value', 'label'} or
                    any(type(group[key]) is not str or not group[key] for key in ('value', 'label'))):
                raise ValueError('presentation_groups')
            names.append(group['value'])
        if len(names) != len(set(names)) or {'admin', 'ignore'} & set(names) or set(grouped) - {'general'} - set(names):
            raise ValueError('presentation_groups')
        object.__setattr__(self, '_trial_id', trial_id)
        object.__setattr__(self, '_patient_info', patient_info)
        object.__setattr__(self, '_snapshot', encoded)

    def require_binding(self, trial_id, patient_info):
        if type(trial_id) is not int or trial_id != self._trial_id or patient_info is not self._patient_info:
            raise ValueError('presentation_binding')

    def view(self, name):
        if type(name) is not str or name not in ('potential', 'grouped', 'group_names'):
            raise ValueError('presentation_view')
        return json.loads(self._snapshot)[name]
