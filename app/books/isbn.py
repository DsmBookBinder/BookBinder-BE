"""ISBN validation and canonicalisation for book identity."""


def normalize_isbn(value: str) -> str:
    """Return a valid ISBN-13, converting a valid ISBN-10 when necessary.

    Spaces and hyphens are accepted as presentation separators. Other invalid
    characters and incorrect check digits raise ValueError.
    """
    if not isinstance(value, str):
        raise ValueError("ISBN must be a string")
    isbn = value.replace(" ", "").replace("-", "").upper()
    if len(isbn) == 10:
        if not isbn[:9].isascii() or not isbn[:9].isdigit() or isbn[-1] not in "0123456789X":
            raise ValueError("Invalid ISBN-10")
        digits = [int(c) for c in isbn[:9]] + [10 if isbn[-1] == "X" else int(isbn[-1])]
        if sum((10 - i) * digit for i, digit in enumerate(digits)) % 11:
            raise ValueError("Invalid ISBN-10 checksum")
        prefix = "978" + isbn[:9]
        check = (10 - sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(prefix)) % 10) % 10
        return prefix + str(check)
    if len(isbn) != 13 or not isbn.isascii() or not isbn.isdigit() or not isbn.startswith(("978", "979")):
        raise ValueError("Invalid ISBN-13")
    if sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(isbn)) % 10:
        raise ValueError("Invalid ISBN-13 checksum")
    return isbn
