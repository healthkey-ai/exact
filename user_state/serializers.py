import json

from rest_framework import serializers

from user_state.models import TrialSearchPreferences

# The filter vocabulary is finite and small: `StudyPreferences` has 20 fields,
# plus `sort` and `type` for the sort control and the tab. These caps are
# roughly three times and a hundred times that, so no reader meets them —
# they are here because this is the first thing in EXACT anyone can write,
# the column is opaque by design, and "opaque" must not come to mean "a place
# to put anything". Django's 2.5 MB body limit is the only other bound, and
# 2.5 MB of filters per identity is not a bound.
MAX_PREFERENCE_KEYS = 64
MAX_PREFERENCE_BYTES = 16 * 1024
# A filter value is a string, a number, a boolean or a list of those. Three
# levels is already more than the vocabulary uses.
#
# This cap is about SHAPE, not about the crash. It runs after `JSONParser`
# has built the object, so it only ever sees bodies the parser survived —
# which are not the ones that used to 500. That is `BoundedJSONParser`'s job
# and it is wired on the view. An earlier comment here claimed the parser
# "cannot" be fixed and used this cap as the answer; measured, a 20 KB body
# still returned 500 with the cap in place.
MAX_PREFERENCE_DEPTH = 3


def _depth(value, limit):
    """How deep, stopping at `limit`. Iterative: a recursive depth check on a
    deeply nested value is the same crash it is meant to report."""
    depth = 0
    layer = [value]
    while layer and depth <= limit:
        nxt = []
        for item in layer:
            if isinstance(item, dict):
                nxt.extend(item.values())
            elif isinstance(item, (list, tuple)):
                nxt.extend(item)
        if not nxt:
            break
        depth += 1
        layer = nxt
    return depth


class TrialSearchPreferencesSerializer(serializers.ModelSerializer):
    non_default_filter_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = TrialSearchPreferences
        fields = (
            'preferences',
            'weights_wizard_offered',
            'non_default_filter_count',
            'updated_at',
        )
        read_only_fields = ('non_default_filter_count', 'updated_at')

    def validate_preferences(self, value):
        # An object, not a list or a scalar. The column is opaque, but it is
        # opaque the way a dict is: `non_default_filter_count` iterates it,
        # and the page reads named keys off it.
        if not isinstance(value, dict):
            raise serializers.ValidationError('Expected an object of filter values.')
        if len(value) > MAX_PREFERENCE_KEYS:
            raise serializers.ValidationError(
                f'At most {MAX_PREFERENCE_KEYS} filters.'
            )
        # Measured on the payload, not on the request: the body also carries
        # the wizard flag and whatever a future field adds, and it is this
        # column that has to live with the size. `default=str` so a value the
        # JSON parser produced but `json.dumps` would refuse cannot turn a
        # 400 into a 500.
        if _depth(value, MAX_PREFERENCE_DEPTH) > MAX_PREFERENCE_DEPTH:
            raise serializers.ValidationError(
                f'Filters may nest at most {MAX_PREFERENCE_DEPTH} deep.'
            )
        if len(json.dumps(value, default=str)) > MAX_PREFERENCE_BYTES:
            raise serializers.ValidationError(
                f'Filters must serialize to under {MAX_PREFERENCE_BYTES} bytes.'
            )
        return value
