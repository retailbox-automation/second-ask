"""Read first: code decides the price, Nemotron reads the customer.

customer reply -> opt-out gate (code) -> Nemotron 3 Super reads the reply into Facts (the frozen v4 reader) ->
offer() (code: price from the shop's menu, never below the owner's floor; timing; dropped items; stop) -> Nemotron 3
Super writes the message around that fixed offer -> code checks every $ amount and date in the text against the offer
(any mismatch: the code template) -> owner one-tap approval for a concession below list -> booking (Super tool loop).
Silence is decided by code only (the silence branches of SmartRules), with no model call.

Nothing here changes `second_ask/` or `second_read/`: both are imported, never copied.
"""
