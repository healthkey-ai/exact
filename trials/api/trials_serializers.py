from rest_framework import serializers

from trials.models import Trial


class TrialSerializer(serializers.ModelSerializer):
    trialId = serializers.IntegerField(source='id')
    studyId = serializers.CharField(source='study_id')
    briefTitle = serializers.CharField(source='brief_title')
    officialTitle = serializers.CharField(source='official_title')
    phase = serializers.JSONField(source='phases')
    disease = serializers.CharField()
    recruitingStatus = serializers.CharField(source='recruitment_status')
    location = serializers.JSONField(source='locations_name')
    interventionTreatments = serializers.JSONField(source='intervention_treatments')
    postedDate = serializers.DateField(source='posted_date')
    lastUpdateDate = serializers.DateField(source='last_update_date')
    firstEnrolment = serializers.DateField(source='first_enrolment_date')
    enrollmentCount = serializers.IntegerField(source='enrollment_count')
    patientBurdenScore = serializers.IntegerField(source='patient_burden_score')
    sponsor = serializers.CharField(source='sponsor_name')
    goodnessScore = serializers.IntegerField(source='goodness_score')
    trialType = serializers.StringRelatedField(source='trial_type')

    class Meta:
        model = Trial
        fields = [
            'trialId',
            'studyId',
            'briefTitle',
            'officialTitle',
            'phase',
            'disease',
            'recruitingStatus',
            'location',
            'interventionTreatments',
            'postedDate',
            'lastUpdateDate',
            'firstEnrolment',
            'enrollmentCount',
            'patientBurdenScore',
            'sponsor',
            'link',
            'goodnessScore',
            'trialType',
        ]

    def to_representation(self, instance):
        response = super().to_representation(instance)
        patient_info = self.context.get('patient_info')
        search_type = self.context.get('search_type')
        counts = self.context.get('counts') or {}

        if search_type == 'eligible' or getattr(instance, 'match_score', None) == 100:
            attrs_to_fill_in = []
        else:
            attrs_to_fill_in = instance.attrs_to_fill_in(counts)

        response['attributesToFillIn'] = attrs_to_fill_in
        response['matchScore'] = getattr(instance, 'match_score', None)
        response['stage'] = ', '.join([f'Stage {x}' for x in (instance.stages or [])])

        recruitment_status = self.context.get('recruitment_status')
        geo_point = patient_info.geo_point if patient_info else None
        sorted_locations = instance.sorted_locations_by_distance(
            geo_point,
            recruitment_status=recruitment_status
        )
        response['location'] = [x.location.title for x in sorted_locations if x.location]

        if sorted_locations and sorted_locations[0].location and sorted_locations[0].location.geo_point:
            geo = sorted_locations[0].location.geo_point
            response['closestLocationGeoPoint'] = {'latitude': geo.y, 'longitude': geo.x}
        else:
            response['closestLocationGeoPoint'] = None

        distance_units = self.context.get('distance_units') or 'km'
        try:
            dist = instance.distance.mi if distance_units == 'miles' else instance.distance.km
            response['distance'] = int(dist + 0.5)
            response['distanceUnits'] = distance_units
        except AttributeError:
            try:
                dist = instance.get_distance(patient_info, distance_units, recruitment_status=recruitment_status)
                if dist:
                    response['distance'] = dist
                    response['distanceUnits'] = distance_units
                else:
                    response['distance'] = None
                    response['distanceUnits'] = None
            except AttributeError:
                response['distance'] = None
                response['distanceUnits'] = None

        # `eligible` is a claim about a PERSON: that this patient qualifies.
        # There is no patient in the expression above it, so with none supplied
        # `attrs_to_fill_in` is empty and every trial in the corpus came back
        # `eligible` — a false statement, in a word the reader takes literally,
        # about somebody the request never named (#456).
        #
        # `null` instead. That is also where the detail endpoint is headed on
        # this path — its no-patient branch already sets `matchingType = None`
        # — but it does not get there yet: on this base every patient-less
        # detail request raises before reaching that branch (#455). So this
        # does not YET make the two agree; it stops the list from being the
        # one that speaks and claims the stronger thing.
        #
        # Only covers a request with no `patient_info`. A payload EXACT
        # recognises no key of resolves to a blank PatientInfo — not None —
        # and still answers `eligible` here and on the detail endpoint. That
        # belongs at the resolver, where all three of `matchingType`,
        # `matchScore` and `tabCounts` can agree at once, and is blocked on
        # #455 for the same reason: #466.
        #
        # `not_eligible` reaches this branch from one place only: a saved-ids
        # search that kept a trial the eligibility filter would have dropped
        # (#568). The id set comes from that filter itself, so the mark and
        # the omission are the same judgement — see `_widened_saved`. Every
        # other response carries an empty set and reads exactly as before.
        unmatched = self.context.get('unmatched_trial_ids') or frozenset()
        not_eligible = patient_info is not None and instance.id in unmatched
        response['matchingType'] = (
            None
            if patient_info is None
            else 'not_eligible'
            if not_eligible
            else ('eligible' if not attrs_to_fill_in else 'potential')
        )

        # And the score that goes with that verdict. `match_score` here is the
        # SQL annotation counting which criteria could be EVALUATED; it never
        # compares values, so a trial the patient conflicts with still scores
        # 100 — measured on a widened row, and reproduced with a plain age
        # conflict, not just a disease one. Left alone it is a green pill
        # arguing with the verdict beside it and an export column reading
        # `not_eligible,100`.
        #
        # `0`, not `null`: the matcher does not treat the two as separable.
        # `match_score_and_status` returns the pair `(0, 'not_eligible')`
        # unconditionally — `if has_not_matched: return 0, 'not_eligible'` —
        # so 0 is the score this verdict MEANS, and it is what the detail
        # endpoint sends for the same trial. `null` would have made the list
        # merely less wrong while still disagreeing with the page the card
        # opens; this makes them say the same thing.
        #
        # `attributesToFillIn` is NOT touched here, though it will need to be.
        # It reads "supply these and you become eligible", which is not true
        # of a patient whose supplied value IS the conflict. It is empty on
        # this path today for a reason unrelated to any of that — `counts`
        # reaches the serializer hardcoded to `{}` (#464), so the list emits
        # `[]` for every row — and a guard that cannot be told from its
        # absence is one nobody can check. Tracked instead, against the issue
        # that makes it live.
        if not_eligible:
            response['matchScore'] = 0

        if self.context.get('explain') and patient_info:
            from trials.services.trial_match_explainer import TrialMatchExplainer
            response['matchReasons'] = TrialMatchExplainer(instance, patient_info).explain()
        else:
            response['matchReasons'] = None

        return response


class TrialDetailsSerializer(serializers.ModelSerializer):
    trialId = serializers.IntegerField(source='id')
    studyId = serializers.CharField(source='study_id')
    register = serializers.CharField()
    briefTitle = serializers.CharField(source='brief_title')
    officialTitle = serializers.CharField(source='official_title')
    locationsName = serializers.JSONField(source='location_name')
    interventionTreatments = serializers.JSONField(source='intervention_treatments')
    sponsorName = serializers.CharField(source='sponsor_name')
    researchers = serializers.JSONField()
    link = serializers.CharField()
    submittedDate = serializers.DateField(source='submitted_date')
    postedDate = serializers.DateField(source='posted_date')
    lastUpdateDate = serializers.DateField(source='last_update_date')
    firstEnrolmentDate = serializers.DateField(source='first_enrolment_date')
    targetSampleSize = serializers.IntegerField(source='target_sample_size')
    recruitmentStatus = serializers.CharField(source='recruitment_status')
    studyType = serializers.CharField(source='study_type')
    studyDesign = serializers.CharField(source='study_design')
    phases = serializers.JSONField()
    briefSummary = serializers.CharField(source='brief_summary')
    laySummary = serializers.CharField(source='lay_summary')
    participationCriteria = serializers.CharField(source='participation_criteria')
    trialType = serializers.StringRelatedField(source='trial_type')
    ageMin = serializers.IntegerField(source='age_low_limit')
    ageMax = serializers.IntegerField(source='age_high_limit')
    gender = serializers.CharField()
    consentCapabilityRequired = serializers.BooleanField(source='consent_capability_required')
    noTobaccoUseRequired = serializers.BooleanField(source='no_tobacco_use_required')

    class Meta:
        model = Trial
        fields = [
            'trialId',
            'studyId',
            'register',
            'briefTitle',
            'officialTitle',
            'locationsName',
            'interventionTreatments',
            'sponsorName',
            'researchers',
            'link',
            'submittedDate',
            'postedDate',
            'lastUpdateDate',
            'firstEnrolmentDate',
            'targetSampleSize',
            'recruitmentStatus',
            'studyType',
            'studyDesign',
            'phases',
            'briefSummary',
            'laySummary',
            'participationCriteria',
            'trialType',
            'ageMin',
            'ageMax',
            'gender',
            'consentCapabilityRequired',
            'noTobaccoUseRequired',
        ]

    def to_representation(self, instance):
        response = super().to_representation(instance)
        patient_info = self.context.get('patient_info')
        template = self.context.get('template')
        attrs_to_fill_in = self.context.get('attrs_to_fill_in', [])

        from trials.services.trial_details.trial_templates import TrialTemplates
        tt = TrialTemplates(instance, patient_info)
        details_and_groups = tt.details_and_group_names(template, attrs_to_fill_in)

        response['details'] = details_and_groups['details']
        response['groupNames'] = details_and_groups['group_names']
        response['goodnessScore'] = getattr(instance, 'goodness_score', None)

        # Per-patient match — reported on the detail view (get_trial_by_id), not
        # just trial info + goodness. Uses the conflict-aware Python matcher rather
        # than the list/search `match_score` ANNOTATION: that annotation only counts
        # filled-vs-null attrs and assumes filtered_trials already dropped the
        # not-eligible rows. retrieve returns the trial by id WITHOUT that filter,
        # so a conflicting trial (e.g. wrong disease/age/gender) must be scored by
        # the matcher (which returns 0 / not_eligible on a conflict), not mislabeled
        # eligible by the completeness annotation.
        if patient_info is not None:
            from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
            matcher = UserToTrialAttrMatcher(trial=instance, patient_info=patient_info)
            # one pass for both (instead of trial_match_score() + trial_match_status()) — #201
            response['matchScore'], response['matchingType'] = matcher.match_score_and_status()
            if self.context.get('explain'):
                from trials.services.trial_match_explainer import TrialMatchExplainer
                # reuse the matcher instance rather than building a second one — #201
                response['matchReasons'] = TrialMatchExplainer(instance, patient_info, matcher=matcher).explain()
            else:
                response['matchReasons'] = None
        else:
            response['matchScore'] = None
            response['matchingType'] = None
            response['matchReasons'] = None

        return response
