# Frontier privacy pressure evaluation

`fuzz.py` deterministically exercises 1,000 mixed identifier capsules plus hostile,
format-character, and placeholder-confusion cases. It fails if a supported email, phone
number, private IP, URL, or local path survives compilation, if a credential/quarantine or
reserved marker is exported, or if instruction-like provider output passes validation.

This is a compiler and parser test, not proof that arbitrary prose can be perfectly
de-identified. Names and novel identifier formats remain an operator-classification
and approval concern; declared never-egress categories fail closed.
