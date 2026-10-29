"""Verify selected wheel identity against the requested pin without HTTP/downloads."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import fetch_template_wheels as fetcher
from pip._vendor.packaging.tags import Tag


class WheelIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="HyNomas wheel identity ")
        self.root=Path(self.temp.name)
        (self.root/"runtime/ComfyUI").mkdir(parents=True)
        (self.root/"runtime/ComfyUI/requirements.txt").write_text("comfyui-workflow-templates==1.0\n",encoding="utf-8")
        self.downloads=[]

    def tearDown(self):self.temp.cleanup()

    def run_metadata(self,filename):
        metadata={"info":{"requires_dist":[]},"urls":[{"filename":filename,"yanked":False,"size":1,"url":"https://fixture.invalid/wheel","digests":{"sha256":hashlib.sha256(b"w").hexdigest()}}]}
        def download(url,path,size,sha,workers):
            self.downloads.append(filename)
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b"w")
        with patch.object(fetcher,"ROOT",self.root),patch.object(fetcher,"metadata",return_value=metadata),patch.object(fetcher,"sys_tags",return_value=iter([Tag("py3","none","any")])),patch.object(fetcher,"download",download):
            fetcher.main()

    def test_wrong_project_wheel_is_not_downloaded(self):
        with self.assertRaises(ValueError):self.run_metadata("different_project-1.0-py3-none-any.whl")
        self.assertEqual(self.downloads,[])

    def test_wrong_version_wheel_is_not_downloaded(self):
        with self.assertRaises(ValueError):self.run_metadata("comfyui_workflow_templates-9.9-py3-none-any.whl")
        self.assertEqual(self.downloads,[])

    def test_matching_normalized_project_and_version_are_downloaded(self):
        self.run_metadata("comfyui_workflow_templates-1.0-py3-none-any.whl")
        self.assertEqual(self.downloads,["comfyui_workflow_templates-1.0-py3-none-any.whl"])
        receipt=json.loads((self.root/"evidence/template-wheels.json").read_text())
        self.assertEqual(receipt[0]["package"],"comfyui-workflow-templates")
        self.assertEqual(receipt[0]["version"],"1.0")


if __name__=="__main__":unittest.main(verbosity=2)
