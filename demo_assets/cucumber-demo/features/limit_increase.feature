Feature: Credit Limit Increase
  As a customer service representative
  I want to raise a customer's credit-card limit through the public API
  So that approved limit-change requests are applied predictably and safely

  Background:
    Given the AQE test target is reachable
    And there is at least one ACTIVE credit card in the target

  Scenario: Happy path - small positive delta is applied
    When I request a limit change of +1500 on the active card
    Then the response status is 200
    And the response includes the keys card_id, previous_limit, new_limit, delta
    And the returned delta equals 1500
    And the new_limit equals previous_limit plus 1500

  Scenario: Idempotency-safe - a second positive delta succeeds with consistent state
    When I request a limit change of +500 on the active card
    And I request a limit change of +250 on the same card
    Then the second response status is 200
    And the second response previous_limit equals the first response new_limit

  Scenario: Negative-delta rejection contract (expected to fail today)
    # The target currently accepts negative deltas with HTTP 200. This scenario
    # asserts the CORRECT behaviour (reject with 4xx) and is meant to fail until
    # the engineering team adds server-side input validation. AQE then reports
    # the failure as a REAL_BUG via the Supervisor RCA.
    When I request a limit change of -1000 on the active card
    Then the response status is between 400 and 499
    And no limit change is persisted
