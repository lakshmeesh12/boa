"""Registry of all built-in test suites."""
from __future__ import annotations

from models.schemas import BankingModule, TestCase, TestType

from .customers_suite import CUSTOMERS_SUITE
from .credit_cards_suite import CREDIT_CARDS_SUITE
from .deposits_suite import DEPOSITS_SUITE
from .transactions_suite import TRANSACTIONS_SUITE
from .accounts_suite import ACCOUNTS_SUITE

ALL_SUITES: dict[BankingModule, list[TestCase]] = {
    BankingModule.CUSTOMERS:    CUSTOMERS_SUITE,
    BankingModule.ACCOUNTS:     ACCOUNTS_SUITE,
    BankingModule.CREDIT_CARDS: CREDIT_CARDS_SUITE,
    BankingModule.DEPOSITS:     DEPOSITS_SUITE,
    BankingModule.TRANSACTIONS:  TRANSACTIONS_SUITE,
}


def get_suite(module: BankingModule, test_type: TestType = TestType.ALL) -> list[TestCase]:
    cases = ALL_SUITES.get(module, [])
    if test_type == TestType.ALL:
        return cases
    return [c for c in cases if c.test_type == test_type]


def get_all_cases(modules: list[BankingModule], test_type: TestType = TestType.ALL) -> list[TestCase]:
    out: list[TestCase] = []
    for m in modules:
        out.extend(get_suite(m, test_type))
    return out
