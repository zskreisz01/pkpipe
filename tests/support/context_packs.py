"""Helpers for the context refresh (spec 011): synthetic project notes (invented clients, projects and
people), a scripted model that answers the two summary sessions by output schema, and the files of a
synthetic overlay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

PERSONAL_FOLDER = "1-Projects/Personal"
ENTERPRISE_FOLDER = "1-Projects/Enterprise"
ROUTES = {"context_review": ["discord_main"]}

#: Names that must never leave the machine (all synthetic).
CLIENT = "Brightwater Bank"
PROJECT = "Project Falcon"
PERSON = "Dana Okafor"
SECOND_CLIENT = "Marlow Retail Group"
BODY_ONLY = "Heron"  # appears only in a note body: the session has to report it

PERSONAL_NOTES = {
    "Homelab.md": (
        "---\nstatus: active\n---\nA Raspberry Pi cluster at home. Runs Home Assistant and Grafana in "
        "Docker; scripts are Python. Backups go to a NAS with restic.\n"
    ),
    "Garden-Sensors.md": (
        "---\nstatus: idea\n---\nSoil moisture sensors on ESP32 boards, MQTT to the homelab, a small "
        "dashboard in Grafana.\n"
    ),
}
ENTERPRISE_NOTES = {
    "Falcon.md": (
        f"---\nclient: {CLIENT}\nproject: {PROJECT}\npeople:\n  - {PERSON}\n---\n"
        f"{PROJECT} moves the card payment ledger of {CLIENT} from Oracle to PostgreSQL on Kubernetes. "
        f"{PERSON} is the product owner. Hosting must stay in the EU, PCI DSS applies, team of six.\n"
    ),
    "Data-Platform-Notes.md": (
        f"---\nclient: {SECOND_CLIENT}\n---\nA data platform for {SECOND_CLIENT}: Kafka, Spark and dbt on "
        f"Azure. Internally the team calls it {BODY_ONLY}. Weekly releases, GDPR applies.\n"
    ),
}


def write_notes(vault: Path, folder: str, notes: dict[str, str]) -> None:
    for name, text in notes.items():
        path = vault / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def seed_vault(vault: Path, *, personal: bool = True, enterprise: bool = True) -> None:
    (vault / "0-INBOX").mkdir(parents=True, exist_ok=True)
    (vault / "2-Areas").mkdir(parents=True, exist_ok=True)
    if personal:
        write_notes(vault, PERSONAL_FOLDER, PERSONAL_NOTES)
    if enterprise:
        write_notes(vault, ENTERPRISE_FOLDER, ENTERPRISE_NOTES)


def is_enterprise(schema: dict[str, Any] | None) -> bool:
    return "names" in ((schema or {}).get("properties") or {})


class ContextModel:
    """FakeModel callable (``wants_schema``): the personal and the enterprise summary by output schema.

    ``leaky`` makes the enterprise session ignore its instruction and write the private names into the
    summary (the case code has to catch); ``names`` is what it reports as private (default: all but the
    body-only one, which a weak model forgets); ``fail`` raises for the listed kinds."""

    wants_schema = True

    def __init__(self, *, leaky: bool = False, names: list[str] | None = None, fail: set[str] | None = None):
        self.leaky = leaky
        self.names = names
        self.fail = fail or set()
        self.kinds: list[str] = []
        self.users: list[str] = []

    def __call__(self, system: str, user: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        kind = "enterprise" if is_enterprise(schema) else "personal"
        self.kinds.append(kind)
        self.users.append(user)
        if kind in self.fail:
            from pkpipe.ports import ModelUnavailable

            raise ModelUnavailable("scripted outage")
        if kind == "personal":
            return {
                "overview": "Home infrastructure and small IoT projects on Docker and Python.",
                "projects": [
                    {
                        "name": "Homelab",
                        "status": "active",
                        "summary": "A Raspberry Pi cluster with Home Assistant and Grafana.",
                        "stack": ["Docker", "Python", "Grafana"],
                    },
                    {
                        "name": "Garden-Sensors",
                        "status": "idea",
                        "summary": "Soil sensors on ESP32 reporting over MQTT.",
                        "stack": ["ESP32", "MQTT"],
                    },
                ],
            }
        who = (CLIENT, PROJECT) if self.leaky else ("a retail bank", "the payments programme")
        names = self.names if self.names is not None else [CLIENT, PROJECT, PERSON, SECOND_CLIENT]
        return {
            "overview": f"Payment and data programmes for {who[0]}-type clients on Kubernetes and Azure.",
            "projects": [
                {
                    "name": f"Ledger migration of {who[0]} ({who[1]})",
                    "summary": f"Moves a card payment ledger from Oracle to PostgreSQL; {PERSON} owns it."
                    if self.leaky
                    else "Moves a card payment ledger from Oracle to PostgreSQL.",
                    "stack": ["PostgreSQL", "Kubernetes"],
                    "constraints": ["EU hosting", "PCI DSS"],
                },
                {
                    "name": "Retail data platform",
                    "summary": f"Streaming and batch data platform, internally called {BODY_ONLY}."
                    if self.leaky
                    else "Streaming and batch data platform.",
                    "stack": ["Kafka", "Spark", "dbt", "Azure"],
                    "constraints": ["GDPR"],
                },
            ],
            "names": names,
        }


def overlay_with_terms(root: Path, terms: str = "") -> Path:
    """An overlay directory (the owner's private config repo) with an optional terms file."""
    (root / "security").mkdir(parents=True, exist_ok=True)
    if terms:
        (root / "security" / "query_guard_terms.txt").write_text(terms, encoding="utf-8")
    return root
