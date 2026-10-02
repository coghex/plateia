"""Transport contract and in-memory test adapter; no live chat integration."""
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol


class AdapterError(RuntimeError):
    """History or send evidence is unavailable; callers must not infer success."""


@dataclass
class ReadBatch:
    records: list
    cursor: object


@dataclass
class SendResult:
    outcome: str
    proof: str = ""


class Adapter(Protocol):
    def read_messages(self, target: dict, cursor: object) -> ReadBatch:
        """Read normalized records for a channel/prefix selector in server order."""

    def read_acknowledgements(self, cursor: object) -> ReadBatch:
        """Read normalized, server-attested acknowledgements."""

    def send(self, target: str, text: str, tags: dict) -> SendResult:
        """Submit one logical message; split/number its physical lines if needed."""


def matches(target, selector):
    return target == selector.get("channel") or bool(
        selector.get("prefix") and target.startswith(selector["prefix"]))


class FakeAdapter:
    """Synthetic evidence and programmable outcomes. Never opens files or sockets."""
    def __init__(self):
        self.messages = []
        self.acknowledgements = []
        self.sends = []
        self.message_error = self.ack_error = None
        self.send_result = SendResult("sent")

    def read_messages(self, target, cursor):
        if self.message_error:
            raise AdapterError(self.message_error)
        records = [m for m in self.messages if matches(m["target"], target)]
        return ReadBatch(deepcopy(records[cursor or 0:]), len(records))

    def read_acknowledgements(self, cursor):
        if self.ack_error:
            raise AdapterError(self.ack_error)
        return ReadBatch(deepcopy(self.acknowledgements[cursor or 0:]), len(self.acknowledgements))

    def send(self, target, text, tags):
        self.sends.append({"target": target, "text": text, "tags": deepcopy(tags)})
        if isinstance(self.send_result, BaseException):
            raise self.send_result
        return self.send_result
