import pytest
from hello import is_palindrome


def test_is_palindrome_simple():
    assert is_palindrome("racecar") is True
    assert is_palindrome("radar") is True
    assert is_palindrome("level") is True
    assert is_palindrome("hello") is False
    assert is_palindrome("world") is False


def test_is_palindrome_with_spaces_and_punctuation():
    assert is_palindrome("A man, a plan, a canal: Panama") is True
    assert is_palindrome("Was it a car or a cat I saw?") is True
    assert is_palindrome("No 'x' in Nixon") is True
    assert is_palindrome("This is definitely not a palindrome!") is False


def test_is_palindrome_empty_and_single_char():
    assert is_palindrome("") is True
    assert is_palindrome("a") is True
