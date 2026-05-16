Feature: Credit Limit Increase — Input Validation Contract
  As a security-conscious banking operator
  I want the API to reject obviously invalid limit-change requests
  So that an attacker or a buggy client cannot decrease a customer's limit
  via the increase endpoint

  Background:
    Given the AQE test target is reachable
    And there is at least one ACTIVE credit card in the target

  # NOTE: this scenario is EXPECTED TO FAIL on the current target build.
  # The endpoint currently accepts negative deltas with HTTP 200 — that is
  # the validation gap AQE's Supervisor classifies as a REAL_BUG in the report.
  Scenario: Negative delta should be rejected with 4xx
    When I request a limit change of -1000 on the active card
    Then the response status is between 400 and 499
    And no limit change is persisted
