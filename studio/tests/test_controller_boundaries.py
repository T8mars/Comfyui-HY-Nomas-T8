"""Third-pass controller cases with only local fixture HTTP responses."""
import argparse
import json
import unittest
from pathlib import Path
import test_update_controller as fixture


class ControllerBoundaryTests(fixture.ControllerTests):
    # The original four cache tests stay in their own suite.
    test_check_only_does_not_delay_a_pending_automatic_install = None
    test_fresh_current_version_cache_avoids_network = None
    test_corrupt_check_cache_is_discarded = None
    test_future_check_timestamp_does_not_suppress_updates = None

    def release_size(self, value):
        path = self.root / 'release.json'
        releases = json.loads(path.read_text(encoding='utf-8'))
        releases[0]['assets'][0]['size'] = value
        path.write_text(json.dumps(releases), encoding='utf-8')

    def log(self):
        path = self.root / 'calls.log'
        return path.read_text(encoding='utf-8-sig') if path.exists() else ''

    def settings(self, value):
        path = self.root / 'config/update-settings.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(value),encoding='utf-8')

    def test_actual_manifest_size_must_match_release_metadata(self):
        self.release_size(1)
        self.run_mode('auto')
        self.assertNotIn('win64.zip',self.log(),'Controller parsed a manifest whose actual bytes disagree with metadata')

    def test_exact_manifest_size_reaches_archive_request(self):
        self.run_mode('auto')
        self.assertIn('win64.zip',self.log())

    def test_string_false_does_not_silently_disable_updates(self):
        self.settings({'auto_update':'false'})
        self.run_mode('auto')
        self.assertTrue(self.log(),'Wrong-type false silently disabled all checks')

    def test_fractional_interval_is_not_silently_rounded(self):
        self.settings({'interval_hours':1.5})
        self.state(days=-(1.75/24))
        result=self.run_mode('auto')
        self.assertIn('interval_hours',result.stdout,'Invalid interval was silently coerced rather than diagnosed')
        self.assertFalse(self.log(),'Invalid interval should use the default 24-hour cache')

    def test_valid_disabled_setting_still_prevents_network(self):
        self.settings({'auto_update':False})
        self.run_mode('auto')
        self.assertFalse(self.log())

    def test_disabled_setting_is_honored_even_with_invalid_interval(self):
        self.settings({'auto_update':False,'interval_hours':1.5})
        self.run_mode('auto')
        self.assertFalse(self.log())

    def test_corrupt_settings_do_not_permanently_disable_checks(self):
        self.settings({})
        (self.root/'config/update-settings.json').write_text('{broken',encoding='utf-8')
        self.run_mode('auto')
        self.assertTrue(self.log())

    def test_boolean_interval_is_not_coerced_to_one_hour(self):
        self.settings({'interval_hours':True})
        self.state(days=-(2/24))
        self.run_mode('auto')
        self.assertFalse(self.log())


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline-ref')
    parser.add_argument('--report')
    args=parser.parse_args()
    fixture.BASELINE=args.baseline_ref
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ControllerBoundaryTests))
    if args.report:
        path=Path(args.report);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(dict(passed=result.wasSuccessful(),tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),baseline=args.baseline_ref),indent=2),encoding='utf-8')
    raise SystemExit(not result.wasSuccessful())
