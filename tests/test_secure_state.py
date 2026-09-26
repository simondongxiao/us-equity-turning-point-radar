import io
import tempfile
import tarfile
import unittest
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from scripts.secure_state import ROOT, pack

class SecureStateTests(unittest.TestCase):
    def test_private_archive_authenticated_roundtrip(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'outputs') as tmp:
            p=Path(tmp)/'private.json';p.write_text('{"forecast":0.73}',encoding='utf-8')
            plain=pack([p]); key=Fernet.generate_key(); cipher=Fernet(key).encrypt(plain)
            self.assertNotIn(b'forecast',cipher)
            with self.assertRaises(InvalidToken):Fernet(Fernet.generate_key()).decrypt(cipher)
            with tarfile.open(fileobj=io.BytesIO(Fernet(key).decrypt(cipher)),mode='r:gz') as tar:
                self.assertEqual(tar.extractfile(tar.getmembers()[0]).read(),p.read_bytes())

if __name__=='__main__':unittest.main()
