"""Runtime is per invocation and never includes queue or review waits."""
from datetime import datetime,timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app


class RuntimeTests(unittest.TestCase):
    def test_live_and_finished_invocations(self):
        row={'status':'RUNNING','started_at':'2026-10-14T09:00:00+00:00','ended_at':None}
        current=datetime(2026,10,14,10,2,3,tzinfo=timezone.utc)
        self.assertEqual(app.execution_runtime(row,current),(3723,True))
        for state in ['WAITING_REVIEW','SUCCEEDED','FAILED','STOPPED','ARCHIVING','ARCHIVED']:
            with self.subTest(state=state):
                ended={**row,'status':state,'ended_at':'2026-10-14T09:05:04+00:00'}
                self.assertEqual(app.execution_runtime(ended,current),(304,False))
        # Naive historical timestamps are interpreted as UTC.
        self.assertEqual(app.execution_runtime({**row,'status':'SUCCEEDED','started_at':'2026-10-14T09:00:00','ended_at':'2026-10-14T09:00:02'}),(2,False))

    def test_queue_unstarted_invalid_and_retry(self):
        row={'status':'QUEUED','started_at':'2026-10-14T09:00:00+00:00','ended_at':None}
        self.assertEqual(app.execution_runtime(row),(None,False))
        self.assertEqual(app.execution_runtime({**row,'status':'STOPPED','ended_at':'2026-10-14T12:00:00+00:00','last_error':'stopped before start'}),(None,False))
        for changed in [{'status':'STOPPED','started_at':None},{'status':'FAILED','started_at':'invalid'},{'status':'SUCCEEDED','ended_at':'2026-10-14T08:00:00+00:00'}]:
            self.assertEqual(app.execution_runtime({**row,**changed}),(None,False))
        retry={**row,'status':'RUNNING','started_at':'2026-10-14T10:00:00+00:00'}
        self.assertEqual(app.execution_runtime(retry,datetime(2026,10,14,10,0,5,tzinfo=timezone.utc)),(5,True))


if __name__=='__main__':unittest.main()
