"""Only validated profile JSON is accepted from an archive."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from profiles import read_profile_import

class ImportTests(unittest.TestCase):
    def test_valid_json_zip_and_rejected_members(self):
        profile = {'version':1, 'name':'Arcade', 'size':[100,100], 'regions':{'jacket':[0,0,1,1]}}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'profiles.zip'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('folder/profile.json', json.dumps(profile))
                z.writestr('readme.txt', 'not a profile')
            self.assertEqual(read_profile_import(path), [profile])
            for member, contents in [('../escape.json', json.dumps(profile)), ('C:/escape.json', json.dumps(profile)),
                                      ('bad.json','{}'), ('large.json',' '* (2*1024*1024+1))]:
                with zipfile.ZipFile(path,'w') as z:
                    z.writestr('valid.json',json.dumps(profile))
                    z.writestr(member,contents)
                with self.assertRaises(ValueError):
                    read_profile_import(path)
            with zipfile.ZipFile(path,'w') as z:
                link=zipfile.ZipInfo('link.json'); link.external_attr=0o120777<<16
                z.writestr(link,'target')
            with self.assertRaises(ValueError): read_profile_import(path)
            self.assertEqual(list(Path(d).iterdir()), [path])
            path.write_bytes(b'not a zip')
            with self.assertRaises(ValueError): read_profile_import(path)
