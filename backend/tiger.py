"""
Tiger Brokers — disabled.
Tiger's programmatic trading API requires institutional access.
SGX auto-trading uses Futu OpenD instead (see futu_broker.py).
"""


def is_available() -> bool:
    return False


def is_sandbox() -> bool:
    return True
