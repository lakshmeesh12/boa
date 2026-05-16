Feature: Credit Limit Increase — Happy Path
  As a customer service representative
  I want to raise an active customer's credit limit through the public API
  So that approved limit-change requests are applied predictably

  Background:
    Given the AQE test target is reachable
    And there is at least one ACTIVE credit card in the target

  Scenario: Small positive delta is accepted and applied
    When I request a limit change of +1500 on the active card
    Then the response status is 200
    And the response includes the keys card_id, previous_limit, new_limit, delta
    And the returned delta equals 1500
    And the new_limit equals previous_limit plus 1500
