import hmac


def verify(token: str, secret: str) -> bool:
    expected = "sig-" + secret
    return hmac.compare_digest(token, expected)
