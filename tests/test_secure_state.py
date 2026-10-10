import io
import tempfile
import tarfile
import unittest
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from scripts.secure_state import ROOT, archive_paths, is_allowed_archive_member, pack

class SecureStateTests(unittest.TestCase):
    def test_archive_includes_weekly_pool_and_weekly_review_state(self):
        relative = {path.relative_to(ROOT).as_posix() for path in archive_paths()}
        self.assertIn("data/weekly_pool_audit.json", relative)
        self.assertTrue(any(name.startswith("state/weekly-review-") for name in relative))

    def test_restore_namespace_is_narrow(self):
        self.assertTrue(is_allowed_archive_member("state/model_card.json"))
        self.assertTrue(is_allowed_archive_member("data/raw_prices/AAPL.csv"))
        self.assertTrue(is_allowed_archive_member("data/weekly_pool_audit.json"))
        self.assertFalse(is_allowed_archive_member("data/universe_seed.csv"))
        self.assertFalse(is_allowed_archive_member("site/data.json"))

    def test_private_archive_authenticated_roundtrip(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'outputs') as tmp:
            p=Path(tmp)/'private.json';p.write_text('{"forecast":0.73}',encoding='utf-8')
            plain=pack([p]); key=Fernet.generate_key(); cipher=Fernet(key).encrypt(plain)
            self.assertNotIn(b'forecast',cipher)
            with self.assertRaises(InvalidToken):Fernet(Fernet.generate_key()).decrypt(cipher)
            with tarfile.open(fileobj=io.BytesIO(Fernet(key).decrypt(cipher)),mode='r:gz') as tar:
                self.assertEqual(tar.extractfile(tar.getmembers()[0]).read(),p.read_bytes())

if __name__=='__main__':unittest.main()
