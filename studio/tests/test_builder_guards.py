"""Program producer/consumer boundaries and recoverable publication failures."""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('program_builder',Path(__file__).resolve().parents[1]/'build_program_release.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class BuilderGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='HyNomas build guards ')
        self.root=Path(self.temp.name)
        self.stage=self.root/'stage';self.stage.mkdir()
        self.output=self.root/'output';self.output.mkdir()

    def tearDown(self): self.temp.cleanup()

    def test_namespace_rejected_by_real_updater_is_rejected_by_builder(self):
        for name in ['scripts/compiled.pyc','extra.cmd','custom_nodes/ComfyUI-HyNomas-T8/models/user.json','runtime/ComfyUI/user/workflow.json']:
            with self.subTest(name=name),self.assertRaises(ValueError): builder.validate_payload_name(name)

    def test_regular_tracked_source_stays_readable(self):
        path=self.root/'source.txt';path.write_bytes(b'public source')
        self.assertEqual(builder.read_tracked_source(path,self.root),b'public source')

    @unittest.skipUnless(os.name=='nt','Windows junction boundary')
    def test_junction_source_is_rejected_without_reading_external_bytes(self):
        source=self.root/'source';source.mkdir()
        outside=self.root/'outside';outside.mkdir();(outside/'file.txt').write_bytes(b'private fixture')
        link=source/'link';script=self.root/'junction.ps1'
        script.write_text('param($Link,$Target)\nNew-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n',encoding='utf-8-sig')
        subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),str(link),str(outside)],check=True,capture_output=True)
        try:
            with self.assertRaises(ValueError): builder.read_tracked_source(link/'file.txt',source)
            self.assertEqual((outside/'file.txt').read_bytes(),b'private fixture')
        finally: os.rmdir(link)

    def assets(self):
        names=['a.zip','b.json','c.sha256']
        for name in names:
            (self.output/name).write_text('old '+name)
            (self.stage/name).write_text('new '+name)
        return names

    def test_promotion_failure_restores_all_previous_assets(self):
        names=self.assets();replace=Path.replace
        def fail(path,target):
            if path.parent==self.stage and path.name=='b.json': raise OSError('owned fixture promotion failure')
            return replace(path,target)
        with patch.object(Path,'replace',fail),self.assertRaises(OSError): builder.publish_assets(self.stage,self.output,names)
        for name in names: self.assertEqual((self.output/name).read_text(),'old '+name)

    def test_failed_rollback_keeps_explicit_backup(self):
        names=self.assets();replace=Path.replace
        def fail(path,target):
            if (path.parent==self.stage and path.name=='b.json') or (path.parent==self.stage/'previous' and path.name=='a.zip'):
                raise OSError('owned fixture publication and rollback failure')
            return replace(path,target)
        with patch.object(Path,'replace',fail),self.assertRaisesRegex(RuntimeError,'backups retained'):
            builder.publish_assets(self.stage,self.output,names)
        self.assertEqual((self.stage/'previous/a.zip').read_text(),'old a.zip')


if __name__=='__main__': unittest.main(verbosity=2)
