import factory

from trials.models import (
    Country,
    Disease,
    Location,
    PreExistingConditionCategory,
    PreferredCountry,
    State,
    Therapy,
    TherapyComponent,
    Trial,
    TrialPreExistingCondition,
    TrialPurpose,
    TrialType,
    TrialTypeDiseaseConnection,
)


class DiseaseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Disease
        django_get_or_create = ('code',)

    code = factory.Sequence(lambda n: f'disease_{n}')
    title = factory.Sequence(lambda n: f'Disease {n}')


class TrialTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TrialType

    code = factory.Sequence(lambda n: f'trial_type_code_{n}')
    title = factory.Sequence(lambda n: f'Trial Type {n}')


class TrialPurposeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TrialPurpose
        django_get_or_create = ('code',)

    code = factory.Sequence(lambda n: f'trial_purpose_code_{n}')
    title = factory.Sequence(lambda n: f'Trial Purpose {n}')


class TrialTypeDiseaseConnectionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TrialTypeDiseaseConnection
        django_get_or_create = ('trial_type', 'disease')

    trial_type = factory.SubFactory(TrialTypeFactory)
    disease = factory.SubFactory(DiseaseFactory)


class TrialFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Trial

    trial_type = factory.SubFactory(TrialTypeFactory)
    code = factory.Sequence(lambda n: f'trial_code_{n}')
    study_id = factory.Sequence(lambda n: f'trial_{n}')
    brief_title = factory.Sequence(lambda n: f'Trial # {n}')
    register = 'clinicaltrials.gov'
    recruitment_status = 'RECRUITING'
    disease = 'Multiple Myeloma'
    pulmonary_function_test_result_required = False
    bone_imaging_result_required = False
    consent_capability_required = False
    caregiver_availability_required = False
    contraceptive_use_requirement = False
    negative_pregnancy_test_result_required = False
    stages = []
    has_stages = False
    no_plasma_cell_leukemia_required = False
    plasma_cell_leukemia_required = False
    stem_cell_transplant_history_required = False
    concomitant_medications_excluded = []
    concomitant_medications_washout_period_duration = None
    measurable_disease_iwcll_required = False
    hepatomegaly_required = False
    autoimmune_cytopenias_refractory_to_steroids_required = False
    lymphadenopathy_required = False
    splenomegaly_required = False
    bone_marrow_involvement_required = False


class PreferredCountryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PreferredCountry
        django_get_or_create = ('title',)

    title = 'USA'


class CountryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Country
        django_get_or_create = ('title',)

    title = 'USA'


class StateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = State
        django_get_or_create = ('title', 'country')

    country = factory.SubFactory(CountryFactory)
    title = 'TX'


class LocationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Location
        django_get_or_create = ('title',)

    country = factory.SubFactory(CountryFactory)
    state = factory.SubFactory(StateFactory)
    city = 'dallas'
    title = factory.LazyAttribute(
        lambda o: ', '.join(
            [o.city.lower()] + ([str(o.state).lower()] if o.state else []) + [str(o.country).lower()]
        )
    )


class TherapyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Therapy

    code = factory.Sequence(lambda n: f'therapy_{n}')
    title = factory.Sequence(lambda n: f'Therapy # {n}')


class TherapyComponentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TherapyComponent

    code = factory.Sequence(lambda n: f'component_{n}')
    title = factory.Sequence(lambda n: f'Component # {n}')


class PreExistingConditionCategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PreExistingConditionCategory

    code = factory.Sequence(lambda n: f'pre_existing_condition_category_{n}')
    title = factory.Sequence(lambda n: f'PreExistingConditionCategory # {n}')


class TrialPreExistingConditionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TrialPreExistingCondition

    trial = factory.SubFactory(TrialFactory)
    category = factory.SubFactory(PreExistingConditionCategoryFactory)
