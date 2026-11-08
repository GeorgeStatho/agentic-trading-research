"""due_refresh_companies puts the strategist's manager selection ahead of older queued requests."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import unittest

from db_common import get_connection
from market_db_modules.company_interest import register_interest
from market_db_modules.evidence_refresh import due_refresh_companies, enqueue_refresh
from tests.db_fixtures import temporary_database

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
LIFETIME = 3600
MANAGER = ('pipeline_selection', 'manager')
INDUSTRY = ('pipeline_selection', 'industry:x')
WATCH = ('watchlist', 'w')
OPPORTUNIST = ('opportunist', 'o')


def hours_ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat()


class DueRefreshPriorityTests(unittest.TestCase):
    def setUp(self):
        self.db = temporary_database(self)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO sectors(id,sector_key,name) VALUES (1,'technology','Technology')")
            conn.execute("INSERT INTO industries(id,sector_id,industry_key,name) VALUES (1,1,'chips','Chips')")
        self.next_id = 1

    def company(self, source_key, *interests, request_age, status='pending', next_attempt=None, parent=None):
        """Add a company with one request; each interest is (source, source_key[, selected_hours_ago]).
        An interest selected more than an hour ago has expired (LIFETIME is one hour)."""
        cid, self.next_id = self.next_id, self.next_id + 1
        with closing(get_connection(self.db)) as conn:
            # A parent session needs a whole discovery/package chain; only the column value matters here.
            conn.execute('PRAGMA foreign_keys=OFF')
            conn.execute("INSERT INTO companies(id,industry_id,symbol,name) VALUES (?,1,?,?)",
                         (cid, source_key.upper(), source_key))
            for source, key, *age in interests:
                register_interest(conn, cid, source, key, at=hours_ago(*age or [0]), lifetime_seconds=LIFETIME)
            request = enqueue_refresh(conn, cid, 'pipeline_selection', 'k', at=hours_ago(request_age), parent_session_id=parent)
            conn.execute('UPDATE company_evidence_requests SET status=?,next_attempt_at=? WHERE request_id=?',
                         (status, next_attempt or hours_ago(request_age), request))
            conn.commit()
        return source_key

    def due(self, limit=10):
        return [row['name'] for row in due_refresh_companies(at=NOW.isoformat(), limit=limit, db_path=self.db)]

    def test_manager_selection_beats_an_older_watchlist_request(self):
        self.company('old_watch', WATCH, request_age=10)
        self.company('old_opportunist', OPPORTUNIST, request_age=9)
        self.company('manager', MANAGER, request_age=1)
        self.assertEqual(self.due(limit=1), ['manager'])

    def test_parent_session_request_still_beats_manager_selection(self):
        self.company('manager', MANAGER, request_age=5)
        self.company('parent', WATCH, request_age=1, parent='session:1')
        self.assertEqual(self.due(), ['parent', 'manager'])

    def test_expired_manager_interest_gives_no_priority(self):
        # The manager interest was selected two hours ago, so it lapsed an hour ago; WATCH keeps the company due.
        self.company('old_watch', WATCH, request_age=10)
        self.company('lapsed', (*MANAGER, 2), WATCH, request_age=1)
        self.assertEqual(self.due(), ['old_watch', 'lapsed'])

    def test_expired_manager_interest_does_not_outrank_live_pipeline_selection(self):
        self.company('lapsed', (*MANAGER, 2), WATCH, request_age=5)
        self.company('industry', INDUSTRY, request_age=1)
        self.assertEqual(self.due(), ['industry', 'lapsed'])

    def test_older_request_first_among_manager_selections(self):
        self.company('newer', MANAGER, request_age=1)
        self.company('older', MANAGER, request_age=4)
        self.assertEqual(self.due(), ['older', 'newer'])

    def test_other_pipeline_selection_ranks_between_manager_and_the_rest(self):
        self.company('old_watch', WATCH, request_age=10)
        self.company('old_opportunist', OPPORTUNIST, request_age=9)
        self.company('industry', INDUSTRY, request_age=2)
        self.company('manager', MANAGER, request_age=1)
        self.assertEqual(self.due(), ['manager', 'industry', 'old_watch', 'old_opportunist'])

    def test_a_third_of_the_slots_go_to_the_oldest_waiting_request(self):
        self.company('old_watch', WATCH, request_age=10)
        self.company('m1', MANAGER, request_age=3)
        self.company('m2', MANAGER, request_age=2)
        self.company('m3', MANAGER, request_age=1)
        self.assertEqual(self.due(limit=3), ['m1', 'm2', 'old_watch'])

    def test_existing_filters_still_exclude(self):
        self.company('ok', MANAGER, request_age=1)
        self.company('done', MANAGER, request_age=9, status='completed')
        self.company('later', MANAGER, request_age=8, next_attempt=(NOW + timedelta(hours=1)).isoformat())
        self.company('no_interest', request_age=7)
        self.company('lapsed_only', (*MANAGER, 3), request_age=6)
        self.assertEqual(self.due(), ['ok'])


if __name__ == '__main__':
    unittest.main()
