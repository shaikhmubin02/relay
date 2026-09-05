"""The Strands agent layer.

Relay is one agent with five narrow tools. The split is deliberate:

* the model reads unstructured text (a cancellation note, a volunteer's roster note),
  chooses an order to ask people in, writes the human sentences, and decides when a
  situation needs a person;
* :mod:`relay.operations` decides what is actually permitted and performs every write.

``record_acceptance`` is intentionally *not* in the model's tool list. Assigning a
volunteer happens only when that volunteer clicks their own signed link. The model
cannot put anyone on a rota.
"""

from .runner import AgentRun, run_agent  # noqa: F401
