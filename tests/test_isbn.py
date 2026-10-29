import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("isbn", Path(__file__).resolve().parents[1] / "app/books/isbn.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
normalize_isbn = module.normalize_isbn


class IsbnTests(unittest.TestCase):
    def test_valid_isbn13_and_separators(self):
        self.assertEqual(normalize_isbn("978-0-306-40615-7"), "9780306406157")

    def test_convert_isbn10_including_x(self):
        self.assertEqual(normalize_isbn("0-306-40615-2"), "9780306406157")
        self.assertEqual(normalize_isbn("0-8044-2957-x"), "9780804429573")

    def test_invalid_inputs_are_rejected(self):
        for value in (None, "", "9780306406158", "0306406153", "1234567890128", "９７８０３０６４０６１５７", "978\t0306406157"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_isbn(value)


if __name__ == "__main__":
    unittest.main()
