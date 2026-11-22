"""Exercise the actual profile verifier without starting services or loading a model."""
import ast
import hashlib
import os
from pathlib import Path
import tempfile
import unittest


class ProfileVerificationTests(unittest.TestCase):
    def setUp(self):
        source=Path(os.environ.get('PROFILE_SELECTOR_CANDIDATE','/tmp/heretic-model-wake-candidate')).read_text()
        tree=ast.parse(source)
        verify=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='verify')
        env={'pathlib':__import__('pathlib'),'hashlib':hashlib}
        exec(compile(ast.Module(body=[verify],type_ignores=[]),'<profile verifier>','exec'),env)
        self.verify=env['verify']
        runtime=ast.parse(Path(os.environ.get('NATIVE_RUNTIME_CANDIDATE','/tmp/native-service-wake-candidate')).read_text())
        startup=next(n for n in runtime.body if isinstance(n,ast.Try))
        check=next(n for n in startup.body if isinstance(n,ast.If) and ast.unparse(n.test)=="cfg.get('model_stat')")
        self.startup_check=compile(ast.Module(body=[check],type_ignores=[]),'<runtime artifact verifier>','exec')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'fixture.gguf';self.path.write_bytes(b'fixture model')
        s=self.path.stat()
        self.stat=dict(device=s.st_dev,inode=s.st_ino,size=s.st_size,mtime_ns=s.st_mtime_ns,ctime_ns=s.st_ctime_ns)
        self.registry={'source_sha256':{},'profiles':{'fixture':{'model_path':str(self.path),'model_stat':self.stat}}}

    def check_runtime(self):
        exec(self.startup_check,{'pathlib':__import__('pathlib'),'cfg':{'model_stat':self.stat,'entry':{'model_path':str(self.path)}}})

    def test_runtime_accepts_reboot_device_number_but_rejects_file_changes(self):
        self.stat['device']+=1
        self.check_runtime()
        for key in ('inode','size','mtime_ns','ctime_ns'):
            self.stat[key]+=1
            with self.assertRaises(RuntimeError):self.check_runtime()
            self.stat[key]-=1

    def test_device_renumbering_retains_verified_file(self):
        self.stat['device']+=1
        self.verify(self.registry)

    def test_file_identity_changes_remain_rejected(self):
        for key in ('inode','size','mtime_ns','ctime_ns'):
            with self.subTest(key=key):
                self.stat[key]+=1
                with self.assertRaises(RuntimeError):self.verify(self.registry)
                self.stat[key]-=1

    def test_changed_source_still_rejected(self):
        self.registry['source_sha256']={str(self.path):hashlib.sha256(b'original source').hexdigest()}
        with self.assertRaises(RuntimeError):self.verify(self.registry)

if __name__=='__main__':unittest.main()
