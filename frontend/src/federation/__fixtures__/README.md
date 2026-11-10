# PROMOP descriptor fixture

`promopDescriptor.v1.json` is a curated sample of the descriptor PROMOP serves
at `GET /api/v1/patient-records/writable-fields/` (`X-Patient-Form: 1`), pinned
to the producer SHA recorded in `_meta.sha`.

It exists so EXACT's resolver is tested against the real contract rather than
only hand-written entries. Regenerate it when the pinned PROMOP SHA changes;
`descriptorContract.test.ts` fails on an unknown kind, a missing kind, or a
broken alias.

## Regenerate

From the PROMOP checkout at the SHA being pinned (the `promop` service in the
local stack has Django configured):

```bash
docker compose -f docker-compose.yml -f vocab-e2e.override.yml \
  run --rm --no-deps -v <promop-checkout>:/app \
  -e DJANGO_SETTINGS_MODULE=promop.settings promop python -c "
import django, json; django.setup()
from omop_core.services.write_descriptor import build_writable_field_descriptor
from omop_core.services.patient_form import patient_form_descriptors
print(json.dumps(patient_form_descriptors(build_writable_field_descriptor()),
                 indent=1, sort_keys=True, default=str))
" > /tmp/promop-descriptor.json
```

Then rebuild the fixture: keep every entry whose kind is `alias`,
`explicit-answer`, `negated`, `selectable`, `authored` or `unmapped`; add the
named special cases in `descriptorContract.test.ts` (toxicity, source pairs,
computed inputs, `genetic_mutations`, `cytogenic_markers`, demographics) and
every alias target; strip the keys the resolver does not read; set `_meta.sha`
and `_meta.schema_version` from the producer (`DESCRIPTOR_SCHEMA_VERSION`).

The full descriptor is ~210 KB; this sample is ~39 KB. The producer-side
inventory and contract tests in PROMOP own completeness — this file owns the
seam's behavior.
