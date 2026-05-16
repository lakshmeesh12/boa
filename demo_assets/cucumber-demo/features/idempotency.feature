Feature: Credit Limit Increase — Idempotency & State Carryover
  As a customer service representative
  I want repeated limit-change requests on the same card to behave consistently
  So that retries and double-submits don't corrupt the customer's credit state

  Background:
    Given the AQE test target is reachable
    And there is at least one ACTIVE credit card in the target

  Scenario: Two sequential positive deltas leave the card in a consistent state
    When I request a limit change of +500 on the active card
    And I request a limit change of +250 on the same card
    Then the second response status is 200
    And the second response previous_limit equals the first response new_limit
