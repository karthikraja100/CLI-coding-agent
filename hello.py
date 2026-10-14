import logging

def is_palindrome(s):
    # Remove punctuation and spaces, then convert to lowercase
    cleaned_s = ''.join(char.lower() for char in s if char.isalnum())
    return cleaned_s == cleaned_s[::-1]

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logging.info(is_palindrome('A man, a plan, a canal: Panama'))