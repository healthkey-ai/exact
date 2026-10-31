# Supplied patient-facing Trial presentation (EXACT #661)

This is an **inert, server-internal extension point**. No existing call site opts
in. It changes no package pin, flag, matching decision, API request contract,
patient data or deployed backend selection.

## Why a presentation seam

Supplying clinical verdicts alone cannot bypass the old detail path:
`TrialTemplates.__init__` eagerly constructs `TrialAttributes`, which resolves
user details and value options before a view is chosen. The potential template
then resolves therapy display and matches attributes again. Constructing that
path and overwriting its output afterwards still reads the legacy representation.

An opted-in caller now supplies **both completely prepared patient-facing views**
and their group names through `SuppliedTrialPresentation`. `TrialTemplates`
branches before constructing `TrialAttributes`; supplied mode never calls legacy
patient resolution, metadata/display builders or matchers. This is a
presentation value, not another evaluator, PatientRecord adapter or readiness
registry.

```python
from trials.services.trial_details.supplied_presentation import SuppliedTrialPresentation

presentation = SuppliedTrialPresentation(
    trial.pk,
    resolved_patient,  # the exact same already-resolved object passed below
    potential={
        'general': [],
        'trialEligibilityAttributes': prepared_eligibility_rows,
    },
    grouped={'eligibility': prepared_eligibility_rows},
    group_names=[{'value': 'eligibility', 'label': 'Eligibility'}],
)
result = trial.details_and_group_names(
    resolved_patient, template, attrs_to_fill_in, presentation=presentation,
)
```

The same optional keyword is available directly on `TrialTemplates`. Omitted or
`None` retains the original constructor, matcher and rendering behavior. There
is no partial-row overlay: all three view components are mandatory, and a
missing/invalid value fails rather than falling back to legacy. Explicit empty
lists/maps are valid presentation values; no eligibility is inferred from them.

## Guarantees and limits

- Snapshot accepts only bounded, finite JSON-native data (1 MB, depth 32, 50,000
  values). Types/budgets are checked before serialization, including exact
  compact byte accounting; custom objects and non-string keys are not converted.
  Input and returned views are independent copies. No patient/payload data
  appears in the value's repr or its stable validation error codes.
- Bind to a saved positive Trial PK and the exact supplied patient object before
  legacy construction. Wrong Trial/patient, missing patient and invalid snapshot
  types fail. **This prevents accidental context reuse, not unauthorized access.**
  The caller still owns authorization, publication/evidence identity and
  preparing every view from one final request result.
- `attrs_to_fill_in` does not filter or reclassify supplied views. The caller must
  choose a consumer that supports their statuses. `unavailable`/`unsupported`,
  empty select options, null, zero and false remain intact.
- Every eligibility row in either view (all non-`general` grouped rows) must
  carry a supported `matchingType`. Legacy `all_attributes_in_groups_view()` emits
  none, so a caller cannot wrap that output verbatim: a supplied presentation
  synthesises final per-row statuses.
- Read-only enforcement uses the mapping's policy (`is_computed_value`,
  `under_user_control`, `user_value_readonly`), calculated ULN aliases and
  combined range aliases, plus the **host's** `DERIVED_FIELDS_IN_SAVE` when the
  patient surface declares one. EXACT carries no `PatientInfo` model and does not
  duplicate CB's UI-only `PI_ATTR_READONLY` registry; a host that wants those
  fields protected declares them. Manually controlled answers stay editable;
  unconditional persisted-derived entries stay read-only; nested source subforms
  use the existing `readonly` metadata. Patients edit source inputs, never a
  computed verdict.

  **Known pre-existing divergence, deliberately not "fixed" here.** EXACT's
  legacy `TrialAttributes.get_user_details()` still derives `ureadonly` from
  `is_computed_value` alone, so it renders the four `under_user_control`
  attributes (`meets_lugano`, `meets_gelf`, `meets_meas_or_bone_status`,
  `abnormal_kappa_lambda_ratio`) read-only. CancerBot, the upstream source of
  truth, already treats `under_user_control` as user-editable and pins that with
  `test_manually_entered_lugano_and_gelf_values_remain_editable`. This seam
  follows upstream; the stale legacy line is a missed port that belongs in its
  own change, not in this inert extension point. Flipping it back would lock
  answers CB lets patients edit.
- “Complete views” means complete prepared rendering inputs, **not** proof of
  clinical criterion completeness, mapping approval or authenticated readiness.
  The class does not evaluate or cross-certify the clinical content of two views.

## Next integration

Later work connects an evaluated request result to EXACT's native consumers,
including their presentation/response compatibility and permissions. That work
must supply the statuses, source/subform metadata and read-only flags described
above; this seam validates them but never invents them. Trusted
evidence/publication belongs to EXACT #662, and cross-host/shadow/performance
validation to #663.
